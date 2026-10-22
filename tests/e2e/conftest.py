import os

import pytest

# Playwright's sync API runs an event loop in the test thread; Django's ORM refuses to run
# there unless told it is safe (the test owns that thread and the loop is idle).
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


@pytest.fixture
def browser_context_args(browser_context_args: dict) -> dict:
    return {
        **browser_context_args,
        "viewport": {"width": 390, "height": 844},
        "locale": "pt-BR",
        "timezone_id": "America/Sao_Paulo",
    }
