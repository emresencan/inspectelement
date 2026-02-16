from __future__ import annotations

import re
from typing import Iterable


def ensure_java_imports(source: str, required_imports: Iterable[str]) -> str:
    required = sorted({item.strip() for item in required_imports if item and item.strip()})
    if not required and "import " not in source:
        return source

    line_ending = "\r\n" if "\r\n" in source else "\n"
    class_start = _first_type_declaration_index(source)
    preamble = source[:class_start] if class_start >= 0 else source
    body = source[class_start:] if class_start >= 0 else ""

    import_line_pattern = re.compile(r"(?m)^[ \t]*import\s+(static\s+)?([A-Za-z0-9_.*]+)\s*;\s*$")
    normal_imports: set[str] = set()
    static_imports: set[str] = set()

    for match in import_line_pattern.finditer(preamble):
        import_name = match.group(2).strip()
        if match.group(1):
            static_imports.add(import_name)
        else:
            normal_imports.add(import_name)

    for import_name in required:
        normal_imports.add(import_name)

    stripped_preamble = import_line_pattern.sub("", preamble)

    if not normal_imports and not static_imports:
        rebuilt_preamble = _normalize_blank_lines(stripped_preamble, line_ending)
        return rebuilt_preamble + body

    import_block = _build_import_block(
        normal_imports=sorted(normal_imports),
        static_imports=sorted(static_imports),
        line_ending=line_ending,
    )

    package_pattern = re.compile(r"(?m)^[ \t]*package\s+[A-Za-z0-9_.]+\s*;\s*$")
    package_match = package_pattern.search(stripped_preamble)
    if package_match:
        line_end_idx = stripped_preamble.find("\n", package_match.end())
        package_end = line_end_idx + 1 if line_end_idx >= 0 else len(stripped_preamble)
        head = stripped_preamble[:package_end].rstrip(" \t\r\n")
        tail = stripped_preamble[package_end:].lstrip("\r\n")
        rebuilt_preamble = f"{head}{line_ending}{line_ending}{import_block}{line_ending}"
        if tail:
            rebuilt_preamble += f"{line_ending}{tail}"
        return rebuilt_preamble + body

    tail = stripped_preamble.lstrip("\r\n")
    rebuilt = f"{import_block}{line_ending}"
    if tail:
        rebuilt += f"{line_ending}{tail}"
    return rebuilt + body


def _build_import_block(normal_imports: list[str], static_imports: list[str], line_ending: str) -> str:
    lines: list[str] = []
    for import_name in normal_imports:
        lines.append(f"import {import_name};")
    if normal_imports and static_imports:
        lines.append("")
    for import_name in static_imports:
        lines.append(f"import static {import_name};")
    return line_ending.join(lines)


def _first_type_declaration_index(source: str) -> int:
    pattern = re.compile(r"\b(class|interface|enum|record)\s+[A-Za-z_]\w*")
    match = pattern.search(source)
    if not match:
        return -1
    return match.start()


def _normalize_blank_lines(source: str, line_ending: str) -> str:
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    if line_ending != "\n":
        normalized = normalized.replace("\n", line_ending)
    return normalized
