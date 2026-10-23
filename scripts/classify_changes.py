"""Classify a change as `text-only` or `full` from the list of changed files.

Usage: git diff --name-only <base>...HEAD | python scripts/classify_changes.py
       python scripts/classify_changes.py FILE...

A change is `text-only` when every file is a screen template (src/templates/**/*.html), under
docs/, or a Markdown file. Anything else, or an empty list, is `full`. This is the single place
that decides how much CI runs (see .github/workflows/ci.yml).
"""

import sys
from pathlib import PurePosixPath

TEXT_ONLY = "text-only"
FULL = "full"


def is_text_file(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if not parts:
        return False
    if parts[-1].endswith(".md"):
        return True
    if parts[0] == "docs":
        return True
    return parts[:2] == ("src", "templates") and parts[-1].endswith(".html")


def classify(paths: list[str]) -> str:
    files = [p.strip() for p in paths if p.strip()]
    return TEXT_ONLY if files and all(is_text_file(p) for p in files) else FULL


def main(argv: list[str]) -> int:
    paths = argv[1:] or sys.stdin.read().splitlines()
    print(classify(paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
