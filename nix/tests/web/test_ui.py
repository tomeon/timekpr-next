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
            launch = {
                "args": ["--no-sandbox"],
                "executable_path": os.environ["TIMEKPRW_TEST_CHROMIUM"],
            }
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


def test_user_policy_sources(page, server):
    # runs before anything saves a configuration for bob, which would
    # give him a policy of his own
    sign_in(page, server)
    assert page.locator("#user-list li .tag").all_inner_texts() == [
        "own policy",
        "defaults",
    ]
    page.click("#user-list li:nth-child(2)")
    page.wait_for_selector("#user-detail:not([hidden])")
    page.wait_for_function(
        "document.querySelector('#user-policy').textContent.startsWith('Policy: defaults (')"
    )
    assert page.locator("#delete-policy").is_disabled()
    page.click("#user-list li:nth-child(1)")
    page.wait_for_function(
        "document.querySelector('#user-policy').textContent === 'Policy: own'"
    )
    assert page.locator("#delete-policy").is_enabled()
    # alice's policy holds the week limit and the idle-time setting: they
    # are marked and can be unset; the week limit stays for the later tests
    page.wait_for_selector("#config-form button.unset[data-setting=track_inactive]")
    assert page.locator("#config-form button.unset:visible").count() == 2
    assert page.locator("#config-form label.own").count() == 2
    page.click("#config-form button.unset[data-setting=track_inactive]")
    page.wait_for_function(
        "document.querySelector('#message').textContent === 'Setting taken out of the policy'"
    )
    page.wait_for_function(
        "document.querySelectorAll('#config-form label.own').length === 1"
    )
    assert page.locator("#config-form button.unset:visible").count() == 1
    assert page.inner_text("#user-policy") == "Policy: own"


def test_user_page_and_config_save(page, server):
    sign_in(page, server)
    page.click("#user-list li:nth-child(2)")
    page.wait_for_selector("#user-detail:not([hidden])")
    page.wait_for_function("document.querySelectorAll('#hours-grid td.on').length > 0")
    assert page.inner_text("#user-title") == "bob@idm.nixos.test"
    assert "Session none" in page.inner_text("#user-status").replace("\n", " ")
    assert page.locator("#hours-grid td.on").count() == 7 * 24
    assert page.input_value("#config-form [name=limit_per_week]") == "168:00"

    # cycle Monday's hour 0: allowed -> unaccounted -> forbidden
    cell = page.locator("#hours-grid tbody tr:nth-child(1) td:nth-child(2)")
    cell.click()
    assert "uacc" in cell.get_attribute("class")
    cell.click()
    assert not cell.get_attribute("class")
    page.fill("#config-form [name=limit_1]", "2:30")
    page.click("#config-form button[type=submit]")
    wait_message(page, "Saved")

    # the form shows what came back, and the daemon side got the change
    assert page.input_value("#config-form [name=limit_1]") == "2:30"
    assert page.locator("#hours-grid td.on").count() == 7 * 24 - 1
    _result, _message, info = server.api.getUserConfigurationAndInformation(
        "bob@idm.nixos.test", "F"
    )
    assert info["LIMITS_PER_WEEKDAYS"][0] == 9000
    assert "0" not in info["ALLOWED_HOURS_1"] and "1" in info["ALLOWED_HOURS_1"]

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


def test_groups_page(page, server):
    sign_in(page, server)
    page.click("#nav-groups")
    page.wait_for_selector("#groups-list li")
    assert set(page.locator("#groups-list li .name").all_inner_texts()) == {
        "all",
        "kids",
    }
    kids = page.locator("#groups-list li", has_text="kids")
    text = kids.inner_text().replace("\n", " ")
    assert "overrides: all" in text
    assert "alice" in text and "bob@idm.nixos.test" in text

    kids.click()
    page.wait_for_selector("#group-detail:not([hidden])")
    page.wait_for_function(
        "document.querySelectorAll('#group-hours-grid td.on').length > 0"
    )
    assert page.inner_text("#group-title") == "kids"
    # the same form as a user's, minus the user-only parts
    assert page.locator("#group-config-form [name=hide_tray_icon]").count() == 0
    assert page.locator("#config-form [name=hide_tray_icon]").count() == 1
    assert page.locator("#group-detail #time-left-form").count() == 0
    assert page.locator("#group-config-form [name=track_inactive]").count() == 1
    assert page.locator("#group-hours-grid td.on").count() == 7 * 24
    assert page.input_value("#group-config-form [name=overrides]") == "all"

    # only the overrides are sent, and the list reflects them
    page.fill("#group-config-form [name=overrides]", "all;users")
    page.click("#group-config-form button[type=submit]")
    wait_message(page, "Saved")
    assert page.input_value("#group-config-form [name=overrides]") == "all;users"
    _result, _message, info = server.api.getUserConfigurationAndInformation(
        "@kids", "F"
    )
    assert list(info["OVERRIDES"]) == ["all", "users"]
    page.wait_for_function(
        "[...document.querySelectorAll('#groups-list li')]"
        ".some((li) => li.textContent.includes('overrides: all;users'))"
    )
    page.click("#group-config-form button[type=submit]")
    wait_message(page, "Nothing changed")


def test_add_group_policy(page, server):
    sign_in(page, server)
    page.click("#nav-groups")
    page.wait_for_selector("#groups-list li")
    page.fill("#group-name", "teens")
    page.click("#group-add")
    wait_message(page, "Policy created")
    # the new group is listed and selected
    page.wait_for_function(
        "document.querySelector('#group-title').textContent === 'teens'"
    )
    page.wait_for_selector("#group-detail:not([hidden])")
    assert "teens" in page.locator("#groups-list li .name").all_inner_texts()
    assert page.inner_text("#groups-list li.selected .name") == "teens"
    assert page.input_value("#group-name") == ""
    _result, _message, groups = server.api.getGroupList()
    assert [group[0] for group in groups] == ["all", "kids", "teens"]
    _result, _message, info = server.api.getUserConfigurationAndInformation(
        "@teens", "F"
    )
    assert list(info["OVERRIDES"]) == []


def test_migrate_policies_dry_run(page, server):
    sign_in(page, server)
    page.click("#nav-server")
    page.wait_for_selector("#server-fields tr")
    page.click("#migrate-dry-run")
    page.wait_for_function(
        "document.querySelector('#migrate-result').textContent !== ''"
    )
    assert page.inner_text("#migrate-result") == "Would delete the policies of: carol"
