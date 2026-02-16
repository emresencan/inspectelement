from __future__ import annotations

"""Compatibility shim so imports work from repository root.

This repository uses a src-layout package at ``src/inspectelement``.
When running commands from the parent folder, Python can otherwise treat
this directory as a namespace package and miss submodules.
"""

from pathlib import Path

__version__ = "0.1.0"

_repo_pkg_dir = Path(__file__).resolve().parent
_src_pkg_dir = _repo_pkg_dir / "src" / "inspectelement"
if _src_pkg_dir.is_dir():
    src_path = str(_src_pkg_dir)
    if src_path not in __path__:
        __path__.append(src_path)
