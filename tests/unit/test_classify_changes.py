import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "classify_changes.py"
spec = importlib.util.spec_from_file_location("classify_changes", SCRIPT)
classify_changes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(classify_changes)


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["src/templates/board/home.html"], "text-only"),
        (["src/templates/board/home.html", "README.md", "docs/rules.txt"], "text-only"),
        (["docs/notes/a.png", "CHANGELOG.md"], "text-only"),
        (["src/templates/board/home.html", "src/board/views.py"], "full"),
        (["src/templates/static/app.js"], "full"),
        (["src/board/templates/board/home.html"], "full"),
        (["pyproject.toml"], "full"),
        (["Dockerfile", "README.md"], "full"),
        ([".github/workflows/ci.yml"], "full"),
        ([], "full"),
    ],
)
def test_classify(paths, expected):
    assert classify_changes.classify(paths) == expected


def test_script_reads_stdin_and_arguments():
    run = [sys.executable, str(SCRIPT)]
    stdin = subprocess.run(
        run, input="README.md\nsrc/templates/a.html\n", capture_output=True, text=True, check=True
    )
    args = subprocess.run([*run, "src/board/models.py"], capture_output=True, text=True, check=True)
    assert stdin.stdout.strip() == "text-only"
    assert args.stdout.strip() == "full"
