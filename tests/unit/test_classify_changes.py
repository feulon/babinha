import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "classify_changes.py"

HOME = """{% extends "base.html" %}
{% block content %}
<h1 class="title">Quem vai</h1>
<a href="{% url 'home' %}" hx-post="/entrar/" data-x="1">Entrar na lista</a>
<form method="post" action="/x/"><input name="n" type="text" placeholder="Seu nome"></form>
<p>Total: {{ total }} jogadores</p>
{% endblock %}
"""


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src/templates").mkdir(parents=True)
    (tmp_path / "src/templates/home.html").write_text(HOME)
    (tmp_path / "README.md").write_text("old\n")
    (tmp_path / "src/views.py").write_text("x = 1\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def classify(repo: Path, *changed: str) -> str:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "HEAD"],
        cwd=repo,
        input="\n".join(changed),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def edit_home(repo: Path, old: str, new: str) -> str:
    path = repo / "src/templates/home.html"
    path.write_text(HOME.replace(old, new))
    return "src/templates/home.html"


def test_phrase_swap_is_text_only(repo):
    changed = edit_home(repo, "Entrar na lista", "Quero jogar")
    changed2 = edit_home(repo, "Quem vai", "Quem vem")
    assert classify(repo, changed, changed2) == "text-only"


def test_text_attributes_and_wrapped_lines_are_text_only(repo):
    changed = edit_home(repo, 'placeholder="Seu nome"', 'placeholder="Nome completo"')
    assert classify(repo, changed) == "text-only"


def test_adding_an_if_is_full(repo):
    changed = edit_home(repo, "<p>Total", "{% if total %}<p>Total")
    assert classify(repo, changed) == "full"


def test_changing_an_interpolation_is_full(repo):
    changed = edit_home(repo, "{{ total }}", "{{ total|add:1 }}")
    assert classify(repo, changed) == "full"


def test_changing_an_href_is_full(repo):
    changed = edit_home(repo, "{% url 'home' %}", "/outra/")
    assert classify(repo, changed) == "full"


def test_touching_an_hx_post_is_full(repo):
    changed = edit_home(repo, 'hx-post="/entrar/"', 'hx-post="/sair/"')
    assert classify(repo, changed) == "full"


def test_changing_a_class_is_full(repo):
    changed = edit_home(repo, 'class="title"', 'class="subtitle"')
    assert classify(repo, changed) == "full"


def test_adding_an_element_is_full(repo):
    changed = edit_home(repo, "<p>Total", "<button>Ok</button><p>Total")
    assert classify(repo, changed) == "full"


def test_new_template_is_full(repo):
    (repo / "src/templates/new.html").write_text("<p>oi</p>")
    assert classify(repo, "src/templates/new.html") == "full"


def test_markdown_only_is_text_only(repo):
    (repo / "README.md").write_text("new\n")
    (repo / "docs").mkdir()
    (repo / "docs/a.txt").write_text("a")
    assert classify(repo, "README.md", "docs/a.txt") == "text-only"


def test_text_template_plus_code_is_full(repo):
    changed = edit_home(repo, "Quem vai", "Quem vem")
    assert classify(repo, changed, "src/views.py") == "full"


def test_empty_list_is_full(repo):
    assert classify(repo) == "full"


def test_missing_base_argument_fails(repo):
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=repo, input="", capture_output=True, text=True
    )
    assert result.returncode == 2


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("<p>Total", "<script>go()</script><p>Total"),
        ("<h1", "<style>h1{color:red}</style><h1"),
        ('type="text"', 'type="text" required'),
        ('name="n"', 'name="n" value="2"'),
        ('data-x="1"', 'data-x="1" onclick="x()"'),
        ('data-x="1"', 'data-x="1" hx-on:click="x()"'),
    ],
)
def test_script_style_and_other_attributes_are_full(repo, old, new):
    assert classify(repo, edit_home(repo, old, new)) == "full"


def test_editing_script_body_is_full(repo):
    path = repo / "src/templates/home.html"
    path.write_text(HOME + "<script>a = 1</script>\n")
    git(repo, "commit", "-qam", "script")
    path.write_text(HOME + "<script>a = 2</script>\n")
    assert classify(repo, "src/templates/home.html") == "full"
