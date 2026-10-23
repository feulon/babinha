"""Classify a change as `text-only` or `full` from the list of changed files.

Usage: git diff --name-only <base>...HEAD | python scripts/classify_changes.py <base>

A change is `text-only` when every file is under docs/, a Markdown file, or a screen template
(src/templates/**/*.html) whose structure is unchanged against <base>: the same template tags and
interpolations ({% ... %} and {{ ... }}), the same HTML tags, the same attributes and values, and
the same <script> and <style> blocks. Only the words between tags and the human-readable
attributes (title, alt, placeholder, aria-label) may differ. Anything else, a new or deleted
template, or an empty list is `full`. This is the single place that decides how much CI runs (see
.github/workflows/ci.yml).
"""

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

TEXT_ONLY = "text-only"
FULL = "full"

TOKEN = re.compile(
    r"<(script|style)\b.*?</\1\s*>|\{%.*?%\}|\{\{.*?\}\}|<[^>]+>", re.DOTALL | re.IGNORECASE
)
CONSTRUCT = re.compile(r"\{%.*?%\}|\{\{.*?\}\}", re.DOTALL)
ATTRIBUTE = re.compile(r"([\w:@.-]+)(?:\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+))?")
TEXT_ATTRIBUTES = {"title", "alt", "placeholder", "aria-label"}


def tag_signature(tag: str) -> tuple:
    inner = CONSTRUCT.sub(lambda m: m.group(0).replace(" ", "\0"), tag.strip("<>/ \n"))
    name, _, rest = inner.partition(" ")
    attrs = [
        (n.lower(), v)
        for n, v in ATTRIBUTE.findall(rest.replace("\n", " "))
        if n.lower() not in TEXT_ATTRIBUTES
    ]
    return (name.lower(), tuple(attrs), tuple(CONSTRUCT.findall(tag)))


def skeleton(html: str) -> list:
    tokens = []
    source = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    for match in TOKEN.finditer(source):
        token = match.group(0)
        if match.group(1) or token[:2] in ("{%", "{{"):
            tokens.append(token)
        else:
            tokens.append(tag_signature(token))
    return tokens


def is_text_file(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(parts) and (parts[-1].endswith(".md") or parts[0] == "docs")


def is_template(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return parts[:2] == ("src", "templates") and parts[-1].endswith(".html")


def git_show(base: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{base}:{path}"], capture_output=True, text=True, check=False
    )
    return result.stdout if result.returncode == 0 else None


def read_new(path: str) -> str | None:
    try:
        return Path(path).read_text()
    except OSError:
        return None


def is_text_change(path: str, base: str) -> bool:
    if is_text_file(path):
        return True
    if not is_template(path):
        return False
    old, new = git_show(base, path), read_new(path)
    return old is not None and new is not None and skeleton(old) == skeleton(new)


def classify(paths: list[str], base: str) -> str:
    files = [p.strip() for p in paths if p.strip()]
    return TEXT_ONLY if files and all(is_text_change(p, base) for p in files) else FULL


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: classify_changes.py <base-ref> < changed-files", file=sys.stderr)
        return 2
    print(classify(sys.stdin.read().splitlines(), argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
