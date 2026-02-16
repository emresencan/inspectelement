from __future__ import annotations

from dataclasses import dataclass
from difflib import unified_diff
import os
from pathlib import Path
import re
import tempfile
from typing import Sequence

SAFE_PARSE_ERROR = "Could not safely locate class body/markers; no changes applied."
SUPPORTED_ACTIONS: tuple[str, ...] = (
    "clickElement",
    "javaScriptClicker",
    "getText",
    "getAttribute",
    "isElementDisplayed",
    "isElementEnabled",
    "scrollToElement",
    "javaScriptClearAndSetValue",
    "javaScriptGetInnerText",
    "javaScriptGetValue",
)


@dataclass(frozen=True, slots=True)
class JavaPatchResult:
    ok: bool
    changed: bool
    message: str
    updated_source: str
    final_locator_name: str | None
    added_methods: tuple[str, ...]
    added_method_signatures: tuple[str, ...]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class JavaPreview:
    ok: bool
    target_file: Path
    message: str
    diff_text: str
    final_locator_name: str | None
    added_methods: tuple[str, ...]
    added_method_signatures: tuple[str, ...]
    original_source: str | None
    updated_source: str | None
    notes: tuple[str, ...] = ()


def prepare_java_patch(
    source: str,
    locator_name: str,
    selector_type: str,
    selector_value: str,
    actions: Sequence[str],
    log_language: str = "TR",
) -> JavaPatchResult:
    normalized_log_language = _normalize_log_language(log_language)
    notes: list[str] = []
    class_span = _find_primary_class_span(source)
    if class_span is None:
        return JavaPatchResult(
            ok=False,
            changed=False,
            message=SAFE_PARSE_ERROR,
            updated_source=source,
            final_locator_name=None,
            added_methods=(),
            added_method_signatures=(),
            notes=(),
        )

    class_name, open_brace_index, close_brace_index = class_span
    class_inner = source[open_brace_index + 1 : close_brace_index]
    class_inner = _ensure_regions(class_inner, class_name)

    by_expression = _selector_to_by_expression(selector_type, selector_value)
    if by_expression is None:
        return JavaPatchResult(
            ok=False,
            changed=False,
            message="Selected locator type is not supported for Java write.",
            updated_source=source,
            final_locator_name=None,
            added_methods=(),
            added_method_signatures=(),
            notes=(),
        )

    updated_source = source[: open_brace_index + 1] + class_inner + source[close_brace_index:]

    requested_locator_name = locator_name.strip().upper() or "ELEMENT"
    existing_constant = _find_existing_locator_constant(updated_source, by_expression)
    locator_constant = existing_constant
    if existing_constant:
        notes.append(f"Selector already exists as {existing_constant}; reusing.")
    else:
        locator_constant = _resolve_unique_locator_name(updated_source, requested_locator_name)
        if locator_constant != requested_locator_name:
            notes.append(f"Name exists; using {locator_constant}")
        locator_line = f"private final By {locator_constant} = {by_expression};"
        updated_source = _insert_region_entry(updated_source, "AUTO_LOCATORS", locator_line)
        if updated_source is None:
            return JavaPatchResult(
                ok=False,
                changed=False,
                message=SAFE_PARSE_ERROR,
                updated_source=source,
                final_locator_name=None,
                added_methods=(),
                added_method_signatures=(),
                notes=(),
            )

    added_methods: list[str] = []
    added_method_signatures: list[str] = []
    actions_normalized = _normalize_actions(actions)
    for action in actions_normalized:
        method_name = _resolve_unique_method_name(updated_source, _method_base_name(action, locator_constant))
        method_signature = _build_method_signature(page_class_name=class_name, action=action, method_name=method_name)
        method_body = _build_method_snippet(
            class_name,
            action,
            method_name,
            locator_constant,
            normalized_log_language,
        )
        patched = _insert_region_entry(updated_source, "AUTO_ACTIONS", method_body)
        if patched is None:
            return JavaPatchResult(
                ok=False,
                changed=False,
                message=SAFE_PARSE_ERROR,
                updated_source=source,
                final_locator_name=None,
                added_methods=(),
                added_method_signatures=(),
                notes=(),
            )
        updated_source = patched
        added_methods.append(method_name)
        added_method_signatures.append(method_signature)

    if updated_source == source:
        if notes:
            message = " ".join(notes)
        else:
            message = "No changes generated."
        return JavaPatchResult(
            ok=True,
            changed=False,
            message=message,
            updated_source=source,
            final_locator_name=locator_constant,
            added_methods=tuple(added_methods),
            added_method_signatures=tuple(added_method_signatures),
            notes=tuple(notes),
        )

    message = "Preview generated — no files written."
    if notes:
        message = f"{message} {' '.join(notes)}"

    return JavaPatchResult(
        ok=True,
        changed=True,
        message=message,
        updated_source=updated_source,
        final_locator_name=locator_constant,
        added_methods=tuple(added_methods),
        added_method_signatures=tuple(added_method_signatures),
        notes=tuple(notes),
    )


def generate_java_preview(
    target_file: Path,
    locator_name: str,
    selector_type: str,
    selector_value: str,
    actions: Sequence[str],
    log_language: str = "TR",
) -> JavaPreview:
    try:
        original_source = target_file.read_text(encoding="utf-8")
    except OSError as exc:
        return JavaPreview(
            ok=False,
            target_file=target_file,
            message=f"Could not read target file: {exc}",
            diff_text="",
            final_locator_name=None,
            added_methods=(),
            added_method_signatures=(),
            original_source=None,
            updated_source=None,
            notes=(),
        )

    patch = prepare_java_patch(
        source=original_source,
        locator_name=locator_name,
        selector_type=selector_type,
        selector_value=selector_value,
        actions=actions,
        log_language=log_language,
    )

    if not patch.ok:
        return JavaPreview(
            ok=False,
            target_file=target_file,
            message=patch.message,
            diff_text="",
            final_locator_name=None,
            added_methods=(),
            added_method_signatures=(),
            original_source=original_source,
            updated_source=None,
            notes=patch.notes,
        )

    if not patch.changed:
        return JavaPreview(
            ok=False,
            target_file=target_file,
            message=patch.message,
            diff_text="",
            final_locator_name=patch.final_locator_name,
            added_methods=patch.added_methods,
            added_method_signatures=patch.added_method_signatures,
            original_source=original_source,
            updated_source=original_source,
            notes=patch.notes,
        )

    diff = "".join(
        unified_diff(
            original_source.splitlines(keepends=True),
            patch.updated_source.splitlines(keepends=True),
            fromfile=str(target_file),
            tofile=str(target_file),
            lineterm="",
        )
    )

    return JavaPreview(
        ok=True,
        target_file=target_file,
        message=patch.message,
        diff_text=diff,
        final_locator_name=patch.final_locator_name,
        added_methods=patch.added_methods,
        added_method_signatures=patch.added_method_signatures,
        original_source=original_source,
        updated_source=patch.updated_source,
        notes=patch.notes,
    )


def apply_java_preview(preview: JavaPreview) -> tuple[bool, str, Path | None]:
    if not preview.ok or preview.updated_source is None or preview.original_source is None:
        return False, "No preview to apply.", None

    target_file = preview.target_file
    try:
        current_source = target_file.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"Could not read target file before apply: {exc}", None

    if current_source != preview.original_source:
        return (
            False,
            "Target file changed after preview. Regenerate preview before apply.",
            None,
        )

    backup_path = Path(f"{target_file}.bak")
    try:
        backup_path.write_text(current_source, encoding="utf-8")
    except OSError as exc:
        return False, f"Could not create backup: {exc}", None

    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=f".{target_file.name}.", suffix=".tmp", dir=str(target_file.parent))
        temp_path = Path(temp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            temp_file.write(preview.updated_source)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        temp_path.replace(target_file)
    except OSError as exc:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return False, f"Could not write target file: {exc}", None

    return True, f"Applied. Backup created at {backup_path}", backup_path


def _find_primary_class_span(source: str) -> tuple[str, int, int] | None:
    class_match = re.search(r"\bpublic\s+class\s+([A-Za-z_]\w*)\b", source)
    if not class_match:
        return None

    class_name = class_match.group(1)
    open_brace_index = source.find("{", class_match.end())
    if open_brace_index < 0:
        return None

    close_brace_index = _find_matching_brace(source, open_brace_index)
    if close_brace_index is None:
        return None

    return class_name, open_brace_index, close_brace_index


def _find_matching_brace(text: str, open_brace_index: int) -> int | None:
    if open_brace_index >= len(text) or text[open_brace_index] != "{":
        return None

    depth = 0
    for index in range(open_brace_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _ensure_regions(class_inner: str, class_name: str) -> str:
    updated = class_inner

    if _find_region(updated, "AUTO_LOCATORS") is None:
        insertion_index = _constructor_end_index(updated, class_name)
        if insertion_index is None:
            insertion_index = 0
        locators_block = (
            "\n"
            "    // region AUTO_LOCATORS\n"
            "    // endregion AUTO_LOCATORS\n"
            "\n"
        )
        updated = updated[:insertion_index] + locators_block + updated[insertion_index:]

    if _find_region(updated, "AUTO_ACTIONS") is None:
        trimmed = updated.rstrip()
        tail = "\n" if not updated.endswith("\n") else ""
        actions_block = (
            "\n"
            "    // region AUTO_ACTIONS\n"
            "    // endregion AUTO_ACTIONS\n"
        )
        updated = trimmed + actions_block + tail + "\n"

    return updated


def _constructor_end_index(class_inner: str, class_name: str) -> int | None:
    constructor_pattern = re.compile(rf"\bpublic\s+{re.escape(class_name)}\s*\([^)]*\)\s*\{{")
    constructor_match = constructor_pattern.search(class_inner)
    if not constructor_match:
        return None

    open_brace_index = constructor_match.end() - 1
    close_brace_index = _find_matching_brace(class_inner, open_brace_index)
    if close_brace_index is None:
        return None

    insertion_index = close_brace_index + 1
    while insertion_index < len(class_inner) and class_inner[insertion_index] in {"\r", "\n", "\t", " "}:
        insertion_index += 1
    return insertion_index


@dataclass(frozen=True, slots=True)
class _Region:
    start_content: int
    end_content: int
    indent: str


def _find_region(source: str, region_name: str) -> _Region | None:
    marker_pattern = re.compile(rf"(?m)^(?P<indent>[ \t]*)// region {re.escape(region_name)}[ \t]*$")
    start_match = marker_pattern.search(source)
    if not start_match:
        return None

    end_pattern = re.compile(rf"(?m)^[ \t]*// endregion {re.escape(region_name)}[ \t]*$")
    end_match = end_pattern.search(source, start_match.end())
    if not end_match:
        return None

    start_line_end = source.find("\n", start_match.end())
    if start_line_end == -1:
        start_content = start_match.end()
    else:
        start_content = start_line_end + 1

    end_content = end_match.start()
    return _Region(start_content=start_content, end_content=end_content, indent=start_match.group("indent"))


def _insert_region_entry(source: str, region_name: str, entry: str) -> str | None:
    region = _find_region(source, region_name)
    if region is None:
        return None

    code_indent = f"{region.indent}    "
    entry_lines = entry.splitlines()
    formatted_entry = "\n".join(f"{code_indent}{line}" if line else "" for line in entry_lines) + "\n"

    region_content = source[region.start_content : region.end_content]
    if region_content.strip():
        new_region_content = region_content.rstrip() + "\n\n" + formatted_entry
    else:
        new_region_content = formatted_entry

    return source[: region.start_content] + new_region_content + source[region.end_content :]


def _resolve_unique_locator_name(source: str, desired_name: str) -> str:
    base_name = desired_name.strip().upper() or "ELEMENT"
    if not _contains_word(source, base_name):
        return base_name

    suffix = 2
    while True:
        candidate = f"{base_name}_{suffix}"
        if not _contains_word(source, candidate):
            return candidate
        suffix += 1


def _resolve_unique_method_name(source: str, desired_name: str) -> str:
    if not _contains_method(source, desired_name):
        return desired_name

    suffix = 2
    while True:
        candidate = f"{desired_name}_{suffix}"
        if not _contains_method(source, candidate):
            return candidate
        suffix += 1


def _contains_word(source: str, value: str) -> bool:
    return re.search(rf"\b{re.escape(value)}\b", source) is not None


def _contains_method(source: str, method_name: str) -> bool:
    return re.search(rf"\b{re.escape(method_name)}\s*\(", source) is not None


def _find_existing_locator_constant(source: str, by_expression: str) -> str | None:
    target = _normalize_expression(by_expression)
    locator_pattern = re.compile(
        r"\b(?:private|protected|public)?\s*(?:static\s+)?(?:final\s+)?By\s+([A-Za-z_]\w*)\s*=\s*(By\.[^;]+);",
        re.MULTILINE,
    )
    for match in locator_pattern.finditer(source):
        existing_name = match.group(1)
        existing_expression = match.group(2)
        if _normalize_expression(existing_expression) == target:
            return existing_name
    return None


def _normalize_expression(value: str) -> str:
    return re.sub(r"\s+", "", value)


def build_action_method_signature_preview(page_class_name: str, locator_name: str, action: str) -> str | None:
    normalized_action = _normalize_action_key(action)
    if normalized_action is None:
        return None
    base_name = _method_base_name(normalized_action, locator_name.strip().upper() or "ELEMENT")
    return _build_method_signature(page_class_name=page_class_name, action=normalized_action, method_name=base_name)


def _normalize_actions(actions: Sequence[str]) -> list[str]:
    ordered: list[str] = []
    for action in actions:
        normalized = _normalize_action_key(action)
        if normalized and normalized not in ordered:
            ordered.append(normalized)
    return ordered


def _normalize_action_key(action: str) -> str | None:
    normalized = action.strip()
    alias_map = {
        "click": "clickElement",
        "sendKeys": "javaScriptClearAndSetValue",
    }
    mapped = alias_map.get(normalized, normalized)
    if mapped in SUPPORTED_ACTIONS:
        return mapped
    return None


def _method_base_name(action: str, locator_name: str) -> str:
    pascal_name = _to_pascal_case(locator_name)
    if action == "clickElement":
        return f"click{pascal_name}"
    if action == "javaScriptClicker":
        return f"jsClick{pascal_name}"
    if action == "scrollToElement":
        return f"scrollTo{pascal_name}"
    if action == "getText":
        return f"get{pascal_name}Text"
    if action == "getAttribute":
        return f"get{pascal_name}Attribute"
    if action == "isElementDisplayed":
        return f"is{pascal_name}Displayed"
    if action == "isElementEnabled":
        return f"is{pascal_name}Enabled"
    if action == "javaScriptClearAndSetValue":
        return f"jsSet{pascal_name}"
    if action == "javaScriptGetInnerText":
        return f"jsGet{pascal_name}InnerText"
    if action == "javaScriptGetValue":
        return f"jsGet{pascal_name}Value"
    return f"action{pascal_name}"


def _build_method_signature(page_class_name: str, action: str, method_name: str) -> str:
    if action in {"clickElement", "javaScriptClicker", "scrollToElement"}:
        return f"public {page_class_name} {method_name}()"
    if action == "javaScriptClearAndSetValue":
        return f"public {page_class_name} {method_name}(String value)"
    if action == "getText":
        return f"public String {method_name}()"
    if action == "getAttribute":
        return f"public String {method_name}(String attribute)"
    if action in {"javaScriptGetInnerText", "javaScriptGetValue"}:
        return f"public String {method_name}()"
    if action in {"isElementDisplayed", "isElementEnabled"}:
        return f"public boolean {method_name}(int timeoutSeconds)"
    return f"public {page_class_name} {method_name}()"


def _to_pascal_case(value: str) -> str:
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", value) if part]
    if not parts:
        return "Element"
    return "".join(part[:1].upper() + part[1:].lower() for part in parts)


def _build_method_snippet(
    page_class_name: str,
    action: str,
    method_name: str,
    locator_constant: str,
    log_language: str,
) -> str:
    label = _locator_human_label(locator_constant)
    label_java = _escape_java_string(label)
    signature = _build_method_signature(page_class_name, action, method_name)

    if action == "clickElement":
        description = f"{label} alanına tıklanır." if log_language == "TR" else f"Clicks {label} element."
        log_message = f"{label_java} alanına tıklandı." if log_language == "TR" else f"Clicked {label_java} element."
        return (
            "/**\n"
            f" * {description}\n"
            " *\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            f"    clickElement({locator_constant});\n"
            f"    logPass(\"{log_message}\");\n"
            "    return this;\n"
            "}"
        )

    if action == "javaScriptClicker":
        description = f"{label} alanına JavaScript ile tıklanır." if log_language == "TR" else (
            f"Clicks {label} element via JavaScript."
        )
        log_message = (
            f"{label_java} alanına JavaScript ile tıklandı."
            if log_language == "TR"
            else f"Clicked {label_java} element via JavaScript."
        )
        return (
            "/**\n"
            f" * {description}\n"
            " *\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            f"    javaScriptClicker({locator_constant});\n"
            f"    logPass(\"{log_message}\");\n"
            "    return this;\n"
            "}"
        )

    if action == "scrollToElement":
        description = f"{label} alanına kaydırılır." if log_language == "TR" else f"Scrolls to {label} element."
        log_message = f"{label_java} alanına kaydırıldı." if log_language == "TR" else f"Scrolled to {label_java} element."
        return (
            "/**\n"
            f" * {description}\n"
            " *\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            f"    scrollToElement({locator_constant});\n"
            f"    logPass(\"{log_message}\");\n"
            "    return this;\n"
            "}"
        )

    if action == "javaScriptClearAndSetValue":
        description = (
            f"{label} alanı JavaScript ile temizlenip değer yazılır."
            if log_language == "TR"
            else f"Clears and sets {label} value via JavaScript."
        )
        param_line = " * @param value yazılacak değer" if log_language == "TR" else " * @param value value to set"
        log_message = (
            f"{label_java} alanına JavaScript ile değer yazıldı: "
            if log_language == "TR"
            else f"Set {label_java} value via JavaScript: "
        )
        return (
            "/**\n"
            f" * {description}\n"
            " *\n"
            f"{param_line}\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            f"    javaScriptClearAndSetValue({locator_constant}, value);\n"
            f"    logPass(\"{log_message}\" + value);\n"
            "    return this;\n"
            "}"
        )

    if action == "getText":
        description = f"{label} metnini döndürür." if log_language == "TR" else f"Returns text of {label} element."
        return (
            "/**\n"
            f" * {description}\n"
            " *\n"
            " * @return element text\n"
            " */\n"
            f"{signature} {{\n"
            f"    String text = getText({locator_constant});\n"
            "    return text;\n"
            "}"
        )

    if action == "getAttribute":
        description = f"{label} alanından attribute değeri döndürür." if log_language == "TR" else (
            f"Returns attribute value from {label} element."
        )
        param_line = " * @param attribute attribute adı" if log_language == "TR" else " * @param attribute attribute name"
        return (
            "/**\n"
            f" * {description}\n"
            " *\n"
            f"{param_line}\n"
            " * @return attribute value\n"
            " */\n"
            f"{signature} {{\n"
            f"    String attr = getAttribute({locator_constant}, attribute);\n"
            "    return attr;\n"
            "}"
        )

    if action == "isElementDisplayed":
        description = f"{label} alanı görünür mü kontrol eder." if log_language == "TR" else (
            f"Checks whether {label} element is displayed."
        )
        param_line = " * @param timeoutSeconds bekleme süresi (saniye)" if log_language == "TR" else (
            " * @param timeoutSeconds wait timeout in seconds"
        )
        return (
            "/**\n"
            f" * {description}\n"
            " *\n"
            f"{param_line}\n"
            " * @return true if displayed\n"
            " */\n"
            f"{signature} {{\n"
            f"    boolean ok = isElementDisplayed({locator_constant}, timeoutSeconds);\n"
            "    return ok;\n"
            "}"
        )

    if action == "isElementEnabled":
        description = f"{label} alanı aktif mi kontrol eder." if log_language == "TR" else (
            f"Checks whether {label} element is enabled."
        )
        param_line = " * @param timeoutSeconds bekleme süresi (saniye)" if log_language == "TR" else (
            " * @param timeoutSeconds wait timeout in seconds"
        )
        return (
            "/**\n"
            f" * {description}\n"
            " *\n"
            f"{param_line}\n"
            " * @return true if enabled\n"
            " */\n"
            f"{signature} {{\n"
            f"    boolean ok = isElementEnabled({locator_constant}, timeoutSeconds);\n"
            "    return ok;\n"
            "}"
        )

    if action == "javaScriptGetInnerText":
        description = f"{label} alanının innerText değerini döndürür." if log_language == "TR" else (
            f"Returns JavaScript innerText of {label} element."
        )
        return (
            "/**\n"
            f" * {description}\n"
            " *\n"
            " * @return innerText value\n"
            " */\n"
            f"{signature} {{\n"
            f"    String t = javaScriptGetInnerText({locator_constant});\n"
            "    return t;\n"
            "}"
        )

    if action == "javaScriptGetValue":
        description = f"{label} alanının value değerini döndürür." if log_language == "TR" else (
            f"Returns JavaScript value of {label} element."
        )
        return (
            "/**\n"
            f" * {description}\n"
            " *\n"
            " * @return value attribute\n"
            " */\n"
            f"{signature} {{\n"
            f"    String v = javaScriptGetValue({locator_constant});\n"
            "    return v;\n"
            "}"
        )

    return (
        "/**\n"
        f" * No template found for action: {action}\n"
        " */\n"
        f"{signature} {{\n"
        "    return this;\n"
        "}"
    )


def _normalize_log_language(value: str) -> str:
    normalized = value.strip().upper()
    if normalized in {"EN", "ENG", "ENGLISH"}:
        return "EN"
    return "TR"


def _locator_human_label(locator_constant: str) -> str:
    no_suffix = re.sub(r"_(TXT|BTN|LNK)$", "", locator_constant.strip(), flags=re.IGNORECASE)
    compact = re.sub(r"_+", "_", no_suffix).strip("_")
    if not compact:
        return "ELEMENT"
    return compact.replace("_", " ")


def _selector_to_by_expression(selector_type: str, selector_value: str) -> str | None:
    escaped = _escape_java_string(selector_value)
    normalized = selector_type.strip().lower()

    if normalized == "css":
        return f'By.cssSelector("{escaped}")'
    if normalized == "xpath":
        return f'By.xpath("{escaped}")'
    if normalized == "id":
        return f'By.id("{escaped}")'
    if normalized == "name":
        return f'By.name("{escaped}")'
    return None


def _escape_java_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
