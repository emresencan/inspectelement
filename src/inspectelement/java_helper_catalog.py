from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable


@dataclass(frozen=True, slots=True)
class JavaMethod:
    owner: str
    return_type: str
    name: str
    parameters: tuple[str, ...]
    raw_signature: str


def extract_java_methods(source: str, *, owner_name: str, require_public: bool = False) -> list[JavaMethod]:
    cleaned = _strip_java_comments(source)
    methods: list[JavaMethod] = []
    method_pattern = re.compile(
        r"""
        (?:(?<=^)|(?<=\n)|(?<=[;}]))\s*
        (?:@Override\s*)*
        (?P<modifiers>(?:(?:public|protected|private|default|static|final|synchronized|abstract)\s+)*)
        (?P<return>[A-Za-z_][\w<>\[\], ?.]+)\s+
        (?P<name>[A-Za-z_]\w*)\s*
        \((?P<params>[^)]*)\)\s*
        (?:throws\s+[^{;]+)?
        (?P<end>[;{])
        """,
        re.MULTILINE | re.VERBOSE,
    )
    for match in method_pattern.finditer(cleaned):
        modifiers = match.group("modifiers").strip()
        if require_public and "public" not in modifiers.split():
            continue
        return_type = match.group("return").strip()
        method_name = match.group("name").strip()
        if method_name == owner_name:
            continue
        if not _looks_like_return_type(return_type):
            continue
        params = tuple(_split_parameters(match.group("params").strip()))
        signature = f"{return_type} {method_name}({', '.join(params)})"
        methods.append(
            JavaMethod(
                owner=owner_name,
                return_type=return_type,
                name=method_name,
                parameters=params,
                raw_signature=signature,
            )
        )
    return methods


def extract_java_methods_from_file(path: Path, *, owner_name: str, require_public: bool = False) -> list[JavaMethod]:
    source = path.read_text(encoding="utf-8")
    return extract_java_methods(source, owner_name=owner_name, require_public=require_public)


def build_table_catalog_markdown(
    table_verifier_methods: Iterable[JavaMethod],
    html_table_verifier_methods: Iterable[JavaMethod],
) -> str:
    lines: list[str] = []
    lines.append("# Table Actions Catalog")
    lines.append("")
    lines.append("Generated from `TableVerifier.java` and `HtmlTableVerifier.java` public method signatures.")
    lines.append("")
    lines.append("## TableVerifier (Interface)")
    lines.append("")
    lines.extend(_build_catalog_table_rows(table_verifier_methods, owner="TableVerifier"))
    lines.append("")
    lines.append("## HtmlTableVerifier (Implementation)")
    lines.append("")
    lines.extend(_build_catalog_table_rows(html_table_verifier_methods, owner="HtmlTableVerifier"))
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def build_select_catalog_markdown(select_methods: Iterable[JavaMethod]) -> str:
    lines: list[str] = []
    lines.append("# Select Actions Catalog")
    lines.append("")
    lines.append("Generated from `UniversalSelectHelper.java` public method signatures.")
    lines.append("")
    lines.append("## UniversalSelectHelper")
    lines.append("")
    lines.extend(_build_catalog_table_rows(select_methods, owner="UniversalSelectHelper"))
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def _build_catalog_table_rows(methods: Iterable[JavaMethod], *, owner: str) -> list[str]:
    rows = list(methods)
    rows.sort(key=lambda method: method.name)
    lines: list[str] = []
    lines.append(
        "| Method Signature | Return | Inputs Besides Locator | Only Table Root Locator? | Needs Inner Locator / Row Criteria | Suggestion |"
    )
    lines.append("|---|---|---|---|---|---|")
    for method in rows:
        return_label = _return_label(method.return_type, owner)
        inputs_besides_locator = _inputs_besides_locator(method.parameters)
        inputs_text = ", ".join(inputs_besides_locator) if inputs_besides_locator else "-"
        only_table_root = _only_table_root_capable(method, inputs_besides_locator, owner)
        needs_inner_or_row = _inner_or_row_requirement(method)
        suggestion = _suggestion_tier(method, owner)
        signature = f"{method.return_type} {method.name}({', '.join(method.parameters)})"
        lines.append(
            f"| `{signature}` | {return_label} | {inputs_text} | {only_table_root} | {needs_inner_or_row} | {suggestion} |"
        )
    return lines


def _strip_java_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    source = re.sub(r"//.*?$", "", source, flags=re.MULTILINE)
    return source


def _split_parameters(parameters_raw: str) -> list[str]:
    if not parameters_raw:
        return []
    items: list[str] = []
    current: list[str] = []
    depth = 0
    for char in parameters_raw:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            part = "".join(current).strip()
            if part:
                items.append(part)
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        items.append(tail)
    return items


def _looks_like_return_type(return_type: str) -> bool:
    lower = return_type.strip().lower()
    invalid = {"throw", "return", "new", "if", "for", "while", "switch", "catch", "try"}
    if lower in invalid:
        return False
    return True


def _return_label(return_type: str, owner: str) -> str:
    normalized = return_type.strip()
    if normalized.lower() == "void":
        return "void"
    if normalized == "String":
        return "String"
    if normalized in {"boolean", "Boolean"}:
        return "boolean"
    if normalized == owner or normalized.endswith("Verifier") or normalized.endswith("Helper"):
        return "Fluent"
    return normalized


def _inputs_besides_locator(parameters: Iterable[str]) -> list[str]:
    result: list[str] = []
    for parameter in parameters:
        if "By tableLocator" in parameter:
            continue
        result.append(parameter)
    return result


def _only_table_root_capable(method: JavaMethod, inputs_besides_locator: list[str], owner: str) -> str:
    if owner == "UniversalSelectHelper":
        return "NO"
    if method.name in {"hasAnyRow", "assertHasAnyRow", "inTable"} and not inputs_besides_locator:
        return "YES"
    if method.name in {"hasAnyRow", "assertHasAnyRow"} and inputs_besides_locator == []:
        return "YES"
    if method.name == "inTable":
        return "YES"
    return "NO"


def _inner_or_row_requirement(method: JavaMethod) -> str:
    parameter_text = " ".join(method.parameters)
    needs_inner = "By innerLocator" in parameter_text
    needs_row = method.name.startswith("where") or "columnHeader" in parameter_text
    if needs_inner and needs_row:
        return "Inner locator + row criteria"
    if needs_inner:
        return "Inner locator"
    if needs_row:
        return "Row criteria"
    return "-"


def _suggestion_tier(method: JavaMethod, owner: str) -> str:
    if owner == "UniversalSelectHelper":
        if method.name in {"selectBySelectIdAuto", "selectByLabel", "withWaitBeforeSelect"}:
            return "Common"
        return "Advanced"

    common_table = {
        "assertColumnTextEquals",
        "getColumnText",
        "assertRowExists",
        "hasAnyRow",
        "assertHasAnyRow",
        "filter",
        "clickInFirstRow",
        "clickRadioInRow",
        "clickLink",
        "whereEquals",
        "whereContains",
        "inTable",
    }
    if method.name in common_table:
        return "Common"
    return "Advanced"
