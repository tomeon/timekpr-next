"""The daemon/JSON conversions shared by timekprw and timekpra's HTTP connector."""

from fake import LIVE, default_group, default_user

from timekpr.common.utils import webapi
from timekpr.web.bridge import plain


def test_user_config_round_trip():
    info = plain(default_user())
    config = webapi.user_config_from_daemon(info)
    back = webapi.user_config_to_daemon(config)
    expected = {
        key: value
        for key, value in info.items()
        if not key.startswith(("TIME_", "POLICY_"))
    }
    assert back == expected
    # in the daemon's key order, so that timekpra prints the same thing
    assert list(back) == list(expected)
    assert "hide_tray_icon" in config and "overrides" not in config


def test_user_policy_round_trip():
    info = plain(default_user())
    info["POLICY_SOURCE"], info["POLICY_GROUPS"] = "user", ["all", "kids"]
    info["POLICY_SETTINGS"] = ["limit_per_week"]
    policy = webapi.user_policy_from_daemon(info)
    assert policy == {
        "policy_source": "user",
        "policy_groups": ["all", "kids"],
        "policy_settings": ["limit_per_week"],
    }
    assert webapi.user_policy_to_daemon(policy) == {
        "POLICY_SOURCE": "user",
        "POLICY_GROUPS": ["all", "kids"],
        "POLICY_SETTINGS": ["limit_per_week"],
    }


def test_group_config_round_trip():
    info = plain(default_group())
    info["OVERRIDES"] = ["all"]
    config = webapi.group_config_from_daemon(info)
    assert config["overrides"] == ["all"] and "hide_tray_icon" not in config
    assert list(webapi.GROUP_FIELDS) == [
        "limit_per_week",
        "limit_per_month",
        "track_inactive",
    ]
    back = webapi.group_config_to_daemon(config)
    # the daemon returns HIDE_TRAY_ICON for a group too, but it means nothing
    # there, and the settings the policy holds are not part of the config
    expected = {
        key: value
        for key, value in info.items()
        if key not in ("HIDE_TRAY_ICON", "POLICY_SETTINGS")
    }
    assert back == expected
    assert list(back) == list(expected)
    assert webapi.policy_settings_from_daemon(info) == []


def test_status_round_trip():
    saved = plain(default_user())
    status = webapi.user_status_from_daemon(saved)
    assert status["session_active"] is False and status["time_left_continuous"] is None
    assert webapi.user_status_to_daemon(status) == {
        key: saved[key] for key in webapi.STATUS_SAVED.values()
    }
    assert webapi.user_status_to_daemon(status, saved=False) == {}
    live = dict(saved, **LIVE)
    status = webapi.user_status_from_daemon(live)
    assert status["session_active"] is True and status["time_left_day"] == 14
    assert webapi.user_status_to_daemon(status, saved=False) == LIVE
    assert list(webapi.user_status_to_daemon(status)) == list(
        webapi.STATUS_SAVED.values()
    ) + list(LIVE)


def test_limits_are_positional_against_allowed_days():
    assert webapi.limits_by_day([2, 4], [100, 200, 300]) == {2: 100, 4: 200}
    assert webapi.limits_by_day([2, 4, 6], [100]) == {2: 100, 4: 0, 6: 0}
    assert webapi.limits_list([2, 4], {"4": 3600, 2: 7}) == [7, 3600]
    assert webapi.limits_list([1], {}) == [0]


def test_hours_notation():
    entries = [
        {"hour": 7},
        {"hour": 11, "start_minute": 0, "end_minute": 30},
        {"hour": 14, "unaccounted": True},
    ]
    daemon = webapi.hours_to_daemon(entries)
    assert daemon == {
        "7": {"STARTMIN": 0, "ENDMIN": 60, "UACC": 0},
        "11": {"STARTMIN": 0, "ENDMIN": 30, "UACC": 0},
        "14": {"STARTMIN": 0, "ENDMIN": 60, "UACC": 1},
    }
    assert [
        entry["hour"]
        for entry in webapi.hours_from_daemon({"14": daemon["14"], "7": daemon["7"]})
    ] == [7, 14]


def test_server_config_round_trip():
    from fake import FakeConnector

    info = FakeConnector().server
    assert (
        webapi.server_config_to_daemon(webapi.server_config_from_daemon(info)) == info
    )
