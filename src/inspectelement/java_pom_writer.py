from __future__ import annotations

from dataclasses import dataclass
from difflib import unified_diff
import os
from pathlib import Path
import re
import tempfile
from typing import Sequence

from .import_parser import ensure_java_imports

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
    "tableAssertRowExists",
    "tableHasAnyRow",
    "tableAssertHasAnyRow",
    "tableFilter",
    "tableAssertRowMatches",
    "tableAssertRowAllEquals",
    "tableClickInColumn",
    "tableClickInRow",
    "tableClickButtonInRow",
    "tableSetInputInColumn",
    "tableAssertColumnTextEquals",
    "tableGetColumnText",
    "tableClickInFirstRow",
    "tableClickRadioInRow",
    "tableClickLink",
    "selectBySelectIdAuto",
    "selectByLabel",
)

TABLE_ACTIONS: set[str] = {
    "tableAssertRowExists",
    "tableHasAnyRow",
    "tableAssertHasAnyRow",
    "tableFilter",
    "tableAssertRowMatches",
    "tableAssertRowAllEquals",
    "tableClickInColumn",
    "tableClickInRow",
    "tableClickButtonInRow",
    "tableSetInputInColumn",
    "tableAssertColumnTextEquals",
    "tableGetColumnText",
    "tableClickInFirstRow",
    "tableClickRadioInRow",
    "tableClickLink",
}

SELECT_ACTIONS: set[str] = {"selectBySelectIdAuto", "selectByLabel"}

ELEMENT_LOCATOR_ACTIONS: set[str] = {
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
}


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
    action_parameters: dict[str, str] | None = None,
    table_root_selector_type: str | None = None,
    table_root_selector_value: str | None = None,
    table_root_locator_name: str | None = None,
) -> JavaPatchResult:
    normalized_log_language = _normalize_log_language(log_language)
    parameters = {str(key): str(value) for key, value in (action_parameters or {}).items()}
    notes: list[str] = []
    line_ending = _detect_line_ending(source)
    indent_unit = _detect_indent_unit(source)
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
    class_inner = _ensure_regions(
        class_inner,
        class_name,
        indent_unit=indent_unit,
        line_ending=line_ending,
    )
    updated_source = source[: open_brace_index + 1] + class_inner + source[close_brace_index:]

    actions_normalized = _normalize_actions(actions)
    requested_locator_name = locator_name.strip().upper() or "ELEMENT"

    locator_constant = requested_locator_name
    requires_element_locator = not actions_normalized or any(action in ELEMENT_LOCATOR_ACTIONS for action in actions_normalized)
    if requires_element_locator:
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

        existing_constant = _find_existing_locator_constant(updated_source, by_expression)
        locator_constant = existing_constant or requested_locator_name
        if existing_constant:
            notes.append(f"Selector already exists as {existing_constant}; reusing.")
        else:
            locator_constant = _resolve_unique_locator_name(updated_source, requested_locator_name)
            if locator_constant != requested_locator_name:
                notes.append(f"Name exists; using {locator_constant}")
            locator_line = f"private final By {locator_constant} = {by_expression};"
            updated_source = _insert_region_entry(
                updated_source,
                "AUTO_LOCATORS",
                locator_line,
                indent_unit=indent_unit,
                line_ending=line_ending,
            )
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

    table_locator_constant: str | None = None
    if any(action in TABLE_ACTIONS for action in actions_normalized):
        if not table_root_selector_type or not table_root_selector_value:
            return JavaPatchResult(
                ok=False,
                changed=False,
                message="Table root could not be detected. Please pick the table root container.",
                updated_source=source,
                final_locator_name=None,
                added_methods=(),
                added_method_signatures=(),
                notes=(),
            )

        table_by_expression = _selector_to_by_expression(table_root_selector_type, table_root_selector_value)
        if table_by_expression is None:
            return JavaPatchResult(
                ok=False,
                changed=False,
                message="Table root locator type is not supported for Java write.",
                updated_source=source,
                final_locator_name=None,
                added_methods=(),
                added_method_signatures=(),
                notes=(),
            )

        requested_table_name = (table_root_locator_name or f"{requested_locator_name}_TABLE").strip().upper() or "TABLE"
        if not requested_table_name.endswith("_TABLE"):
            requested_table_name = f"{requested_table_name}_TABLE"
        existing_table_constant = _find_existing_locator_constant(updated_source, table_by_expression)
        table_locator_constant = existing_table_constant or requested_table_name
        if existing_table_constant:
            notes.append(f"Table selector already exists as {existing_table_constant}; reusing.")
        else:
            table_locator_constant = _resolve_unique_locator_name(updated_source, requested_table_name)
            if table_locator_constant != requested_table_name:
                notes.append(f"Table name exists; using {table_locator_constant}")
            table_locator_line = f"private final By {table_locator_constant} = {table_by_expression};"
            updated_source = _insert_region_entry(
                updated_source,
                "AUTO_LOCATORS",
                table_locator_line,
                indent_unit=indent_unit,
                line_ending=line_ending,
            )
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

    if "selectBySelectIdAuto" in actions_normalized:
        select_id = parameters.get("selectId", "").strip()
        if not select_id and selector_type.strip().lower() == "id":
            parameters["selectId"] = selector_value
        elif not select_id:
            return JavaPatchResult(
                ok=False,
                changed=False,
                message="Select Id is required for selectBySelectIdAuto.",
                updated_source=source,
                final_locator_name=None,
                added_methods=(),
                added_method_signatures=(),
                notes=(),
            )

    added_methods: list[str] = []
    added_method_signatures: list[str] = []
    for action in actions_normalized:
        method_base_locator = table_locator_constant if action in TABLE_ACTIONS else locator_constant
        method_name = _resolve_unique_method_name(updated_source, _method_base_name(action, method_base_locator))
        method_signature = _build_method_signature(page_class_name=class_name, action=action, method_name=method_name)
        method_body = _build_method_snippet(
            page_class_name=class_name,
            action=action,
            method_name=method_name,
            locator_constant=locator_constant,
            table_locator_constant=table_locator_constant,
            log_language=normalized_log_language,
            action_parameters=parameters,
        )
        patched = _insert_region_entry(
            updated_source,
            "AUTO_ACTIONS",
            method_body,
            indent_unit=indent_unit,
            line_ending=line_ending,
        )
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

    required_imports = _required_imports_for_actions(actions_normalized)
    updated_source = _ensure_required_imports(updated_source, required_imports)
    updated_source = _finalize_generated_source(updated_source, line_ending)

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
            final_locator_name=table_locator_constant or locator_constant,
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
        final_locator_name=table_locator_constant or locator_constant,
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
    action_parameters: dict[str, str] | None = None,
    table_root_selector_type: str | None = None,
    table_root_selector_value: str | None = None,
    table_root_locator_name: str | None = None,
    source_override: str | None = None,
) -> JavaPreview:
    if source_override is None:
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
    else:
        original_source = source_override

    patch = prepare_java_patch(
        source=original_source,
        locator_name=locator_name,
        selector_type=selector_type,
        selector_value=selector_value,
        actions=actions,
        log_language=log_language,
        action_parameters=action_parameters,
        table_root_selector_type=table_root_selector_type,
        table_root_selector_value=table_root_selector_value,
        table_root_locator_name=table_root_locator_name,
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
    ok, write_message = _write_with_backup_atomic(
        target_file=target_file,
        current_source=current_source,
        updated_source=preview.updated_source,
        backup_path=backup_path,
    )
    if not ok:
        return False, write_message, None

    return True, f"Applied. Backup created at {backup_path}", backup_path


def apply_java_previews(previews: Sequence[JavaPreview]) -> tuple[bool, str, tuple[Path, ...]]:
    valid_previews = [
        preview
        for preview in previews
        if preview.ok and preview.updated_source is not None and preview.original_source is not None
    ]
    if not valid_previews:
        return False, "No preview to apply.", ()

    ordered_targets: list[Path] = []
    base_sources: dict[Path, str] = {}
    final_sources: dict[Path, str] = {}

    for preview in valid_previews:
        target = preview.target_file
        original_source = preview.original_source
        updated_source = preview.updated_source
        if target not in base_sources:
            ordered_targets.append(target)
            base_sources[target] = original_source
            final_sources[target] = updated_source
            continue
        if final_sources[target] != original_source:
            return (
                False,
                f"Queued preview chain mismatch for {target}. Regenerate preview queue before apply.",
                (),
            )
        final_sources[target] = updated_source

    current_sources: dict[Path, str] = {}
    for target in ordered_targets:
        try:
            current_source = target.read_text(encoding="utf-8")
        except OSError as exc:
            return False, f"Could not read target file before apply: {exc}", ()
        current_sources[target] = current_source
        if current_source != base_sources[target]:
            return (
                False,
                f"Target file changed after preview: {target}. Regenerate preview before apply.",
                (),
            )

    backup_paths: list[Path] = []
    for target in ordered_targets:
        backup_path = Path(f"{target}.bak")
        ok, write_message = _write_with_backup_atomic(
            target_file=target,
            current_source=current_sources[target],
            updated_source=final_sources[target],
            backup_path=backup_path,
        )
        if not ok:
            return False, write_message, tuple(backup_paths)
        backup_paths.append(backup_path)

    files_count = len(ordered_targets)
    previews_count = len(valid_previews)
    backups_label = ", ".join(str(path) for path in backup_paths)
    return (
        True,
        f"Applied {previews_count} queued preview(s) across {files_count} file(s). Backup created at {backups_label}",
        tuple(backup_paths),
    )


def _write_with_backup_atomic(
    *,
    target_file: Path,
    current_source: str,
    updated_source: str,
    backup_path: Path,
) -> tuple[bool, str]:
    try:
        backup_path.write_text(current_source, encoding="utf-8")
    except OSError as exc:
        return False, f"Could not create backup: {exc}"

    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=f".{target_file.name}.", suffix=".tmp", dir=str(target_file.parent))
        temp_path = Path(temp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            temp_file.write(updated_source)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        temp_path.replace(target_file)
    except OSError as exc:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return False, f"Could not write target file: {exc}"

    return True, "ok"


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


def _ensure_regions(
    class_inner: str,
    class_name: str,
    *,
    indent_unit: str,
    line_ending: str,
) -> str:
    updated = class_inner

    if _find_region(updated, "AUTO_LOCATORS") is None:
        insertion_index = _constructor_end_index(updated, class_name)
        if insertion_index is None:
            insertion_index = 0
        locators_block = (
            f"{line_ending}"
            f"{indent_unit}// region AUTO_LOCATORS{line_ending}"
            f"{indent_unit}// endregion AUTO_LOCATORS{line_ending}"
            f"{line_ending}"
        )
        updated = updated[:insertion_index] + locators_block + updated[insertion_index:]

    if _find_region(updated, "AUTO_ACTIONS") is None:
        trimmed = updated.rstrip()
        tail = line_ending if not updated.endswith(("\n", "\r")) else ""
        actions_block = (
            f"{line_ending}"
            f"{indent_unit}// region AUTO_ACTIONS{line_ending}"
            f"{indent_unit}// endregion AUTO_ACTIONS{line_ending}"
        )
        updated = trimmed + actions_block + tail + line_ending

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
    marker_pattern = re.compile(rf"(?m)^(?P<indent>[ \t]*)// region {re.escape(region_name)}[ \t]*\r?$")
    start_match = marker_pattern.search(source)
    if not start_match:
        return None

    end_pattern = re.compile(rf"(?m)^[ \t]*// endregion {re.escape(region_name)}[ \t]*\r?$")
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


def _insert_region_entry(
    source: str,
    region_name: str,
    entry: str,
    *,
    indent_unit: str,
    line_ending: str,
) -> str | None:
    region = _find_region(source, region_name)
    if region is None:
        return None

    code_indent = f"{region.indent}{indent_unit}"
    normalized_entry = _normalize_line_endings(entry, "\n")
    entry_lines = normalized_entry.split("\n")
    if entry_lines and entry_lines[-1] == "":
        entry_lines = entry_lines[:-1]
    formatted_lines: list[str] = []
    for line in entry_lines:
        adapted = _adapt_indent_to_style(line, indent_unit)
        formatted_lines.append(f"{code_indent}{adapted}" if adapted else "")
    formatted_entry = line_ending.join(formatted_lines) + line_ending

    region_content = source[region.start_content : region.end_content]
    if region_content.strip():
        new_region_content = region_content.rstrip(" \t\r\n") + f"{line_ending}{line_ending}" + formatted_entry
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


def _required_imports_for_actions(actions: Sequence[str]) -> list[str]:
    imports: list[str] = []
    if any(action in TABLE_ACTIONS for action in actions):
        imports.extend(
            [
                "java.time.Duration",
                "com.turkcell.common.components.table.HtmlTableVerifier",
            ]
        )
    if "tableAssertRowMatches" in actions:
        imports.append("java.util.function.Predicate")
    if "tableAssertRowAllEquals" in actions:
        imports.append("java.util.Map")
    if any(action in SELECT_ACTIONS for action in actions):
        imports.append("com.turkcell.common.components.selectHelper.UniversalSelectHelper")
    return imports


def _ensure_required_imports(source: str, required_imports: Sequence[str]) -> str:
    return ensure_java_imports(source, required_imports)


def _detect_line_ending(source: str) -> str:
    return "\r\n" if "\r\n" in source else "\n"


def _detect_indent_unit(source: str) -> str:
    for line in source.splitlines():
        stripped = line.lstrip(" \t")
        if not stripped:
            continue
        indent = line[: len(line) - len(stripped)]
        if "\t" in indent:
            return "\t"
    return "    "


def _normalize_line_endings(source: str, line_ending: str) -> str:
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    if line_ending == "\n":
        return normalized
    return normalized.replace("\n", line_ending)


def _finalize_generated_source(source: str, line_ending: str) -> str:
    return _normalize_line_endings(source, line_ending)


def _adapt_indent_to_style(line: str, indent_unit: str) -> str:
    if indent_unit == "    ":
        return line
    if not line:
        return line
    stripped = line.lstrip(" ")
    leading_spaces = len(line) - len(stripped)
    if leading_spaces == 0:
        return line
    tabs = leading_spaces // 4
    remainder = leading_spaces % 4
    return f"{indent_unit * tabs}{' ' * remainder}{stripped}"


def build_action_method_signature_preview(
    page_class_name: str,
    locator_name: str,
    action: str,
    *,
    table_locator_name: str | None = None,
    action_parameters: dict[str, str] | None = None,
) -> str | None:
    normalized_action = _normalize_action_key(action)
    if normalized_action is None:
        return None
    base_name_source = table_locator_name if normalized_action in TABLE_ACTIONS else locator_name
    base_name = _method_base_name(normalized_action, (base_name_source or "ELEMENT").strip().upper() or "ELEMENT")
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


def _table_where_method(action_parameters: dict[str, str]) -> str:
    match_type = action_parameters.get("matchType", "equals").strip().lower()
    return "whereContains" if match_type == "contains" else "whereEquals"


def _to_boolean_literal(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return "true"
    return "false"


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
    if action == "tableAssertRowExists":
        return f"assert{pascal_name}RowExists"
    if action == "tableHasAnyRow":
        return f"has{pascal_name}AnyRow"
    if action == "tableAssertHasAnyRow":
        return f"assert{pascal_name}HasAnyRow"
    if action == "tableFilter":
        return f"filter{pascal_name}"
    if action == "tableAssertRowMatches":
        return f"assert{pascal_name}RowMatches"
    if action == "tableAssertRowAllEquals":
        return f"assert{pascal_name}RowAllEquals"
    if action == "tableClickInColumn":
        return f"click{pascal_name}InColumn"
    if action == "tableClickInRow":
        return f"click{pascal_name}InRow"
    if action == "tableClickButtonInRow":
        return f"click{pascal_name}ButtonInRow"
    if action == "tableSetInputInColumn":
        return f"set{pascal_name}InputInColumn"
    if action == "tableAssertColumnTextEquals":
        return f"assert{pascal_name}ColumnTextEquals"
    if action == "tableGetColumnText":
        return f"get{pascal_name}ColumnText"
    if action == "tableClickInFirstRow":
        return f"click{pascal_name}FirstRow"
    if action == "tableClickRadioInRow":
        return f"click{pascal_name}RadioInRow"
    if action == "tableClickLink":
        return f"click{pascal_name}Link"
    if action == "selectBySelectIdAuto":
        return f"select{pascal_name}"
    if action == "selectByLabel":
        return f"select{pascal_name}ByLabel"
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
    if action == "tableAssertRowExists":
        return f"public {page_class_name} {method_name}(String columnHeader, String expectedText, int timeoutSec)"
    if action == "tableHasAnyRow":
        return f"public boolean {method_name}(int timeoutSec)"
    if action == "tableAssertHasAnyRow":
        return f"public {page_class_name} {method_name}(int timeoutSec)"
    if action == "tableFilter":
        return f"public {page_class_name} {method_name}(String columnHeader, String filterText, int timeoutSec)"
    if action == "tableAssertRowMatches":
        return (
            f"public {page_class_name} {method_name}("
            "String columnHeader, Predicate<String> predicate, int timeoutSec)"
        )
    if action == "tableAssertRowAllEquals":
        return f"public {page_class_name} {method_name}(Map<String, String> columnToExpectedText, int timeoutSec)"
    if action == "tableClickInColumn":
        return (
            f"public {page_class_name} {method_name}("
            "String matchColumnHeader, String matchText, String columnHeader, By innerLocator, int timeoutSec)"
        )
    if action in {"tableClickInRow", "tableClickButtonInRow"}:
        return (
            f"public {page_class_name} {method_name}("
            "String matchColumnHeader, String matchText, By innerLocator, int timeoutSec)"
        )
    if action == "tableSetInputInColumn":
        return (
            f"public {page_class_name} {method_name}("
            "String matchColumnHeader, String matchText, String columnHeader, String text, int timeoutSec)"
        )
    if action == "tableAssertColumnTextEquals":
        return (
            f"public {page_class_name} {method_name}("
            "String matchColumnHeader, String matchText, String columnHeader, String expectedText, int timeoutSec)"
        )
    if action == "tableGetColumnText":
        return (
            f"public String {method_name}("
            "String matchColumnHeader, String matchText, String columnHeader, int timeoutSec)"
        )
    if action == "tableClickInFirstRow":
        return f"public {page_class_name} {method_name}(By innerLocator, int timeoutSec)"
    if action == "tableClickRadioInRow":
        return (
            f"public {page_class_name} {method_name}("
            "String matchColumnHeader, String matchText, int timeoutSec)"
        )
    if action == "tableClickLink":
        return f"public {page_class_name} {method_name}(String matchColumnHeader, String matchText, int timeoutSec)"
    if action == "selectBySelectIdAuto":
        return f"public {page_class_name} {method_name}(String optionText)"
    if action == "selectByLabel":
        return f"public {page_class_name} {method_name}(String labelText, String optionText)"
    return f"public {page_class_name} {method_name}()"


def _to_pascal_case(value: str) -> str:
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", value) if part]
    if not parts:
        return "Element"
    return "".join(part[:1].upper() + part[1:].lower() for part in parts)


def _build_method_snippet(
    *,
    page_class_name: str,
    action: str,
    method_name: str,
    locator_constant: str,
    table_locator_constant: str | None,
    log_language: str,
    action_parameters: dict[str, str],
) -> str:
    label = _locator_human_label(locator_constant)
    label_java = _escape_java_string(label)
    signature = _build_method_signature(page_class_name, action, method_name)
    table_constant = table_locator_constant or "TABLE_LOCATOR"
    table_label = _locator_human_label(table_constant)
    table_label_java = _escape_java_string(table_label)

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

    if action == "tableAssertRowExists":
        where_method = _table_where_method(action_parameters)
        return (
            "/**\n"
            f" * Asserts row exists in {table_label} table.\n"
            " *\n"
            " * @param columnHeader table column header\n"
            " * @param expectedText expected cell text\n"
            " * @param timeoutSec timeout seconds\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            "    new HtmlTableVerifier(driver, Duration.ofSeconds(timeoutSec))\n"
            f"        .inTable({table_constant})\n"
            f"        .{where_method}(columnHeader, expectedText)\n"
            "        .assertRowExists();\n"
            '    logPass("Table row exists: " + columnHeader + "=" + expectedText);\n'
            "    return this;\n"
            "}"
        )

    if action == "tableHasAnyRow":
        return (
            "/**\n"
            f" * Checks whether {table_label} table has any data row.\n"
            " *\n"
            " * @param timeoutSec timeout seconds\n"
            " * @return true if any row exists\n"
            " */\n"
            f"{signature} {{\n"
            "    boolean hasAny = new HtmlTableVerifier(driver, Duration.ofSeconds(timeoutSec))\n"
            f"        .inTable({table_constant})\n"
            "        .hasAnyRow();\n"
            "    return hasAny;\n"
            "}"
        )

    if action == "tableAssertHasAnyRow":
        return (
            "/**\n"
            f" * Asserts {table_label} table has at least one row.\n"
            " *\n"
            " * @param timeoutSec timeout seconds\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            "    new HtmlTableVerifier(driver, Duration.ofSeconds(timeoutSec))\n"
            f"        .inTable({table_constant})\n"
            "        .assertHasAnyRow();\n"
            f'    logPass("{table_label_java} tablosunda en az bir satır doğrulandı.");\n'
            "    return this;\n"
            "}"
        )

    if action == "tableFilter":
        return (
            "/**\n"
            f" * Applies table filter in {table_label}.\n"
            " *\n"
            " * @param columnHeader table column header\n"
            " * @param filterText filter value\n"
            " * @param timeoutSec timeout seconds\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            "    new HtmlTableVerifier(driver, Duration.ofSeconds(timeoutSec))\n"
            f"        .inTable({table_constant})\n"
            "        .filter(columnHeader, filterText);\n"
            '    logPass("Table filter applied: " + columnHeader + "=" + filterText);\n'
            "    return this;\n"
            "}"
        )

    if action == "tableAssertRowMatches":
        return (
            "/**\n"
            f" * Asserts row exists in {table_label} using custom predicate.\n"
            " *\n"
            " * @param columnHeader table column header\n"
            " * @param predicate custom match predicate\n"
            " * @param timeoutSec timeout seconds\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            "    new HtmlTableVerifier(driver, Duration.ofSeconds(timeoutSec))\n"
            f"        .inTable({table_constant})\n"
            "        .whereMatches(columnHeader, predicate)\n"
            "        .assertRowExists();\n"
            '    logPass("Table row matches predicate for: " + columnHeader);\n'
            "    return this;\n"
            "}"
        )

    if action == "tableAssertRowAllEquals":
        return (
            "/**\n"
            f" * Asserts row exists in {table_label} using all column/value criteria.\n"
            " *\n"
            " * @param columnToExpectedText column to expected text map\n"
            " * @param timeoutSec timeout seconds\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            "    new HtmlTableVerifier(driver, Duration.ofSeconds(timeoutSec))\n"
            f"        .inTable({table_constant})\n"
            "        .whereAllEquals(columnToExpectedText)\n"
            "        .assertRowExists();\n"
            '    logPass("Table row exists for all expected column values.");\n'
            "    return this;\n"
            "}"
        )

    if action == "tableClickInColumn":
        where_method = _table_where_method(action_parameters)
        return (
            "/**\n"
            f" * Clicks inner locator in target column for matched row in {table_label}.\n"
            " *\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            "    new HtmlTableVerifier(driver, Duration.ofSeconds(timeoutSec))\n"
            f"        .inTable({table_constant})\n"
            f"        .{where_method}(matchColumnHeader, matchText)\n"
            "        .assertRowExists()\n"
            "        .clickInColumn(columnHeader, innerLocator);\n"
            "    return this;\n"
            "}"
        )

    if action == "tableClickInRow":
        where_method = _table_where_method(action_parameters)
        return (
            "/**\n"
            f" * Clicks inner locator in matched row for {table_label}.\n"
            " *\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            "    new HtmlTableVerifier(driver, Duration.ofSeconds(timeoutSec))\n"
            f"        .inTable({table_constant})\n"
            f"        .{where_method}(matchColumnHeader, matchText)\n"
            "        .assertRowExists()\n"
            "        .clickInRow(innerLocator);\n"
            "    return this;\n"
            "}"
        )

    if action == "tableClickButtonInRow":
        where_method = _table_where_method(action_parameters)
        return (
            "/**\n"
            f" * Clicks button locator in matched row for {table_label}.\n"
            " *\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            "    new HtmlTableVerifier(driver, Duration.ofSeconds(timeoutSec))\n"
            f"        .inTable({table_constant})\n"
            f"        .{where_method}(matchColumnHeader, matchText)\n"
            "        .assertRowExists()\n"
            "        .clickButtonInRow(innerLocator);\n"
            "    return this;\n"
            "}"
        )

    if action == "tableSetInputInColumn":
        where_method = _table_where_method(action_parameters)
        return (
            "/**\n"
            f" * Sets input text in matched row/column for {table_label}.\n"
            " *\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            "    new HtmlTableVerifier(driver, Duration.ofSeconds(timeoutSec))\n"
            f"        .inTable({table_constant})\n"
            f"        .{where_method}(matchColumnHeader, matchText)\n"
            "        .assertRowExists()\n"
            "        .setInputInColumn(columnHeader, text);\n"
            "    return this;\n"
            "}"
        )

    if action == "tableAssertColumnTextEquals":
        where_method = _table_where_method(action_parameters)
        return (
            "/**\n"
            f" * Asserts column text equals expected value in {table_label}.\n"
            " *\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            "    new HtmlTableVerifier(driver, Duration.ofSeconds(timeoutSec))\n"
            f"        .inTable({table_constant})\n"
            f"        .{where_method}(matchColumnHeader, matchText)\n"
            "        .assertRowExists()\n"
            "        .assertColumnTextEquals(columnHeader, expectedText);\n"
            "    return this;\n"
            "}"
        )

    if action == "tableGetColumnText":
        where_method = _table_where_method(action_parameters)
        return (
            "/**\n"
            f" * Returns matched row column text from {table_label}.\n"
            " *\n"
            " * @return column text\n"
            " */\n"
            f"{signature} {{\n"
            "    String value = new HtmlTableVerifier(driver, Duration.ofSeconds(timeoutSec))\n"
            f"        .inTable({table_constant})\n"
            f"        .{where_method}(matchColumnHeader, matchText)\n"
            "        .assertRowExists()\n"
            "        .getColumnText(columnHeader);\n"
            "    return value;\n"
            "}"
        )

    if action == "tableClickInFirstRow":
        return (
            "/**\n"
            f" * Clicks inner locator in first row of {table_label}.\n"
            " *\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            "    new HtmlTableVerifier(driver, Duration.ofSeconds(timeoutSec))\n"
            f"        .inTable({table_constant})\n"
            "        .clickInFirstRow(innerLocator);\n"
            "    return this;\n"
            "}"
        )

    if action == "tableClickRadioInRow":
        where_method = _table_where_method(action_parameters)
        return (
            "/**\n"
            f" * Clicks radio input in matched row for {table_label}.\n"
            " *\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            "    new HtmlTableVerifier(driver, Duration.ofSeconds(timeoutSec))\n"
            f"        .inTable({table_constant})\n"
            f"        .{where_method}(matchColumnHeader, matchText)\n"
            "        .assertRowExists()\n"
            "        .clickRadioInRow();\n"
            "    return this;\n"
            "}"
        )

    if action == "tableClickLink":
        where_method = _table_where_method(action_parameters)
        return (
            "/**\n"
            f" * Clicks first link in matched row for {table_label}.\n"
            " *\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            "    new HtmlTableVerifier(driver, Duration.ofSeconds(timeoutSec))\n"
            f"        .inTable({table_constant})\n"
            f"        .{where_method}(matchColumnHeader, matchText)\n"
            "        .assertRowExists()\n"
            "        .clickLink();\n"
            "    return this;\n"
            "}"
        )

    if action == "selectBySelectIdAuto":
        wait_before_select = _to_boolean_literal(action_parameters.get("waitBeforeSelect", "false"))
        select_id = _escape_java_string(action_parameters.get("selectId", ""))
        return (
            "/**\n"
            f" * Selects option for {label} via UniversalSelectHelper.selectBySelectIdAuto.\n"
            " *\n"
            " * @param optionText option text to select\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            "    new UniversalSelectHelper(driver)\n"
            f"        .withWaitBeforeSelect({wait_before_select})\n"
            f"        .selectBySelectIdAuto(\"{select_id}\", optionText);\n"
            f'    logPass("Select yapıldı: {label_java} -> " + optionText);\n'
            "    return this;\n"
            "}"
        )

    if action == "selectByLabel":
        wait_before_select = _to_boolean_literal(action_parameters.get("waitBeforeSelect", "false"))
        return (
            "/**\n"
            f" * Selects option by label for {label}.\n"
            " *\n"
            " * @param labelText select label text\n"
            " * @param optionText option text to select\n"
            " * @return this\n"
            " */\n"
            f"{signature} {{\n"
            "    new UniversalSelectHelper(driver)\n"
            f"        .withWaitBeforeSelect({wait_before_select})\n"
            "        .selectByLabel(labelText, optionText);\n"
            f'    logPass("Select by label yapıldı: " + labelText + " -> " + optionText);\n'
            "    return this;\n"
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
    no_suffix = re.sub(r"_(TXT|BTN|LNK|TABLE)$", "", locator_constant.strip(), flags=re.IGNORECASE)
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
