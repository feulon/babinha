from playwright.sync_api import Page, expect


def expect_signed_in(page: Page) -> None:
    """A tab bar is up, which only a signed-in player with a phone gets."""
    expect(page.locator("nav.tabbar")).to_be_visible()


def tab(page: Page, name: str) -> None:
    """Tap a tab of the bar at the bottom."""
    page.get_by_role("navigation", name="Navegação principal").get_by_role(
        "link", name=name, exact=True
    ).click()


def sign_out(page: Page) -> None:
    """Sign out the way a player does: from the end of Mais."""
    page.once("dialog", lambda dialog: dialog.accept())  # "Sair do Babinha?"
    tab(page, "Mais")
    page.get_by_role("button", name="Sair").click()
