"""Remove Python bytecode and tool cache directories from the project."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CACHE_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".hypothesis",
        ".tox",
        "htmlcov",
    }
)

CACHE_FILE_SUFFIXES = (".pyc", ".pyo")

SKIP_DIR_NAMES = frozenset({".git", ".venv", "venv", "node_modules", "chroma_db"})


def clean(root: Path = PROJECT_ROOT) -> int:
    """Delete cache dirs and bytecode files under root. Returns count removed."""
    removed = 0

    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue

        if path.is_dir() and path.name in CACHE_DIR_NAMES:
            shutil.rmtree(path)
            print(f"removed {path.relative_to(root)}")
            removed += 1
            continue

        if path.is_file() and path.suffix in CACHE_FILE_SUFFIXES:
            path.unlink()
            print(f"removed {path.relative_to(root)}")
            removed += 1

    return removed


def main() -> None:
    count = clean()
    if count:
        print(f"Cleaned {count} cache path(s).")
    else:
        print("No Python cache files found.")
    sys.exit(0)


if __name__ == "__main__":
    main()
