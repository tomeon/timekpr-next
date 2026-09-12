"""The web UI in a headless Chromium driven by Playwright, against
timekprw on a fake daemon connector.  Needs the playwright package and
a browser (PLAYWRIGHT_BROWSERS_PATH); skipped otherwise."""

import os

import pytest
from helpers import STATIC, Server, free_port, wait_for
from timekpr.client.interface.http.administration import timekprAdminHttpConnector

playwright = pytest.importorskip("playwright.sync_api")
TOKEN = "secret"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("ui")
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN + "\n")
    token_file.chmod(0o600)
    port = free_port()
    args = [
        "--listen",
        f"127.0.0.1:{port}",
        "--token-file",
        str(token_file),
        "--static-dir",
        STATIC,
    ]
    server = Server(tmp_path, args)
    server.url = f"http://127.0.0.1:{port}"
    server.api = timekprAdminHttpConnector(server.url, str(token_file), timeout=5)
    wait_for(server, server.api)
    yield server
    server.stop()


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        # The nix sandbox has no user namespaces for Chromium's own sandbox.
        # nixpkgs' browser bundle carries the full Chromium and not the
        # headless shell, so ask for the "chromium" channel (new headless).
        launch = {"args": ["--no-sandbox"], "channel": "chromium"}
        if os.environ.get("TIMEKPRW_TEST_CHROMIUM"):
            launch = {"args": ["--no-sandbox"], "executable_path": os.environ["TIMEKPRW_TEST_CHROMIUM"]}
        browser = p.chromium.launch(**launch)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.errors = errors
    yield page
    page.close()
    assert errors == [], errors


def wait_message(page, text):
    page.wait_for_function(
        f"document.querySelector('#message').textContent === {text!r}"
    )


def sign_in(page, server):
    page.goto(server.url + "/")
    page.wait_for_selector("#page-token:not([hidden])")
    page.fill("#token-form input[name=token]", TOKEN)
    page.click("#token-form button")
    page.wait_for_selector("#user-list li")


def test_token_prompt_and_user_list(page, server):
    page.goto(server.url + "/")
    # without a token the API answers 401 and the UI asks for one
    page.wait_for_selector("#page-token:not([hidden])")
    assert "Unauthorized" in page.inner_text("#message")
    page.fill("#token-form input[name=token]", "wrong")
    page.click("#token-form button")
    page.wait_for_selector("#page-token:not([hidden])")
    page.fill("#token-form input[name=token]", TOKEN)
    page.click("#token-form button")
    page.wait_for_selector("#user-list li")
    assert page.locator("#user-list li .name").all_inner_texts() == [
        "alice",
        "bob@idm.nixos.test",
    ]
    # alice has a session, bob has none
    assert page.locator("#user-list li:nth-child(1) .dot.active").count() == 1
    assert page.locator("#user-list li:nth-child(2) .dot.active").count() == 0
    page.wait_for_function(
        "document.querySelector('#health').textContent.startsWith('daemon')"
    )


def test_user_page_and_config_save(page, server):
    sign_in(page, server)
    page.click("#user-list li:nth-child(2)")
    page.wait_for_selector("#user-detail:not([hidden])")
    page.wait_for_function("document.querySelectorAll('#hours-grid td.on').length > 0")
    assert page.inner_text("#user-title") == "bob@idm.nixos.test"
    assert "Session none" in page.inner_text("#user-status").replace("\n", " ")
    assert page.locator("#hours-grid td.on").count() == 7 * 24
    assert page.input_value("[name=limit_per_week]") == "168:00"

    # cycle Monday's hour 0: allowed -> unaccounted -> forbidden
    cell = page.locator("#hours-grid tbody tr:nth-child(1) td:nth-child(2)")
    cell.click()
    assert "uacc" in cell.get_attribute("class")
    cell.click()
    assert not cell.get_attribute("class")
    page.fill("[name=limit_1]", "2:30")
    page.select_option("[name=lockout_type]", "suspendwake")
    assert not page.is_hidden("#wake-hours")
    page.fill("[name=wake_from]", "7")
    page.fill("[name=wake_to]", "18")
    page.click("#config-form button[type=submit]")
    wait_message(page, "Saved")

    # the form shows what came back, and the daemon side got the change
    assert page.input_value("[name=limit_1]") == "2:30"
    assert page.locator("#hours-grid td.on").count() == 7 * 24 - 1
    assert page.input_value("[name=lockout_type]") == "suspendwake"
    _result, _message, info = server.api.getUserConfigurationAndInformation(
        "bob@idm.nixos.test", "F"
    )
    assert info["LIMITS_PER_WEEKDAYS"][0] == 9000
    assert "0" not in info["ALLOWED_HOURS_1"] and "1" in info["ALLOWED_HOURS_1"]
    assert (
        info["LOCKOUT_TYPE"] == "suspendwake" and info["WAKEUP_HOUR_INTERVAL"] == "7;18"
    )

    # saving again without changes does not call the API
    page.click("#config-form button[type=submit]")
    wait_message(page, "Nothing changed")


def test_time_left_and_daemon_settings(page, server):
    sign_in(page, server)
    page.click("#user-list li:nth-child(1)")
    page.wait_for_selector("#user-detail:not([hidden])")
    page.wait_for_function("document.querySelectorAll('#hours-grid td.on').length > 0")
    assert "Session active" in page.inner_text("#user-status").replace("\n", " ")
    page.fill("#time-left-form [name=amount]", "0:05")
    page.click("#time-left-form button")
    wait_message(page, "Applied")

    page.click("#nav-server")
    page.wait_for_selector("#server-fields tr")
    assert page.input_value("#server-fields [name=poll_time]") == "3"
    page.fill("#server-fields [name=poll_time]", "7")
    page.click("#server-form button[type=submit]")
    wait_message(page, "Saved")
    assert page.input_value("#server-fields [name=poll_time]") == "7"
    _result, _message, config = server.api.getTimekprConfiguration()
    assert config["TIMEKPR_POLLTIME"] == 7

    # a value the daemon refuses is shown, not swallowed
    page.fill("#server-fields [name=log_level]", "2")
    page.click("#server-form button[type=submit]")
    page.wait_for_function(
        "document.querySelector('#message').textContent.includes('Unexpected ERROR')"
    )
