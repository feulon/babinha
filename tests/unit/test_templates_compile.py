from pathlib import Path

import pytest
from django.conf import settings
from django.template import engines

TEMPLATES_DIR = Path(settings.BASE_DIR) / "src" / "templates"
TEMPLATES = sorted(p.relative_to(TEMPLATES_DIR).as_posix() for p in TEMPLATES_DIR.rglob("*.html"))


def test_templates_found():
    assert TEMPLATES


@pytest.mark.parametrize("name", TEMPLATES)
def test_template_compiles(name):
    """Catches unclosed or unknown tags without rendering (no database needed)."""
    engines["django"].get_template(name)
