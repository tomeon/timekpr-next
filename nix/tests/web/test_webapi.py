"""The daemon/JSON conversions shared by timekprw and timekpra's HTTP connector."""

from fake import LIVE, default_user

from timekpr.common.utils import webapi
from timekpr.web.bridge import plain


def test_user_config_round_trip():
    info = plain(default_user())
    config = webapi.user_config_from_daemon(info)
    back = webapi.user_config_to_daemon(config)
    expected = {
        key: value for key, value in info.items() if not key.startswith("TIME_")
    }
    assert back == expected
    # in the daemon's key order, so that timekpra prints the same thing
    assert list(back) == list(expected)


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
