"""The web API against a fake daemon connector, through FastAPI's TestClient."""

import pytest
from conftest import AUTH, TOKEN
from fake import FakeConnector
from fastapi.testclient import TestClient

from timekpr.web import timekprw
from timekpr.web.app import create_app, is_trusted
from timekpr.web.bridge import Bridge

PROBLEM = "application/problem+json"
ALL_DAYS = {str(day): 0 for day in range(1, 8)}


def test_health_and_auth(client):
    assert client.get("/api/v1/health").json()["daemon"] == "ok"
    r = client.get("/api/v1/users")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith(PROBLEM)
    assert r.headers["www-authenticate"] == "Bearer"
    assert (
        client.get(
            "/api/v1/users", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    assert client.get("/api/v1/users", headers=AUTH).status_code == 200


def test_user_list_with_status(client):
    users = client.get("/api/v1/users?include=status", headers=AUTH).json()
    assert [user["username"] for user in users] == ["alice", "bob@idm.nixos.test"]
    assert [user["policy_source"] for user in users] == ["user", "default"]
    assert users[0]["status"]["session_active"] is True
    assert users[1]["status"]["session_active"] is False
    assert users[1]["status"]["time_left_continuous"] is None
    r = client.get("/api/v1/users?include=bogus", headers=AUTH)
    assert r.status_code == 400 and r.json()["errors"][0]["field"] == "include"


def test_user_shapes(client):
    assert client.get("/api/v1/users/nobody", headers=AUTH).status_code == 404
    bob = client.get("/api/v1/users/bob%40idm.nixos.test", headers=AUTH).json()
    assert bob["username"] == "bob@idm.nixos.test"
    alice = client.get("/api/v1/users/alice", headers=AUTH).json()
    assert (alice["policy_source"], alice["policy_groups"]) == ("user", [])
    assert bob["policy_source"] == "default"
    # live counters supersede the saved ones while a session is active
    assert alice["status"]["time_left_day"] == 14
    assert alice["status"]["time_spent_week"] == 3
    assert alice["status"]["time_left_continuous"] == 15
    assert alice["config"]["limits_per_day"] == {str(day): 86400 for day in range(1, 8)}
    assert len(alice["config"]["allowed_hours"]["1"]) == 24


def test_days_and_limits_are_positional(client, fake):
    r = client.patch(
        "/api/v1/users/alice/config", json={"limits_per_day": ALL_DAYS}, headers=AUTH
    )
    assert r.status_code == 200
    assert fake.calls[-1] == (
        "applyPolicyChanges",
        ("alice", [], {"limits_per_day": ALL_DAYS}),
    )
    assert r.json()["limits_per_day"] == ALL_DAYS
    fake.calls.clear()
    # the daemon aligns the limits with the days: day 2 keeps its limit
    patch = {"allowed_days": [4, 2], "limits_per_day": {"4": 3600}}
    r = client.patch("/api/v1/users/alice/config", json=patch, headers=AUTH)
    assert r.status_code == 200
    assert fake.calls == [
        (
            "applyPolicyChanges",
            (
                "alice",
                [],
                {"allowed_days": ["4", "2"], "limits_per_day": {"4": 3600}},
            ),
        ),
    ]
    assert r.json()["allowed_days"] == [2, 4]
    assert r.json()["limits_per_day"] == {"2": 0, "4": 3600}


def test_allowed_hours(client, fake):
    hours = [
        {"hour": 7},
        {"hour": 11, "start_minute": 0, "end_minute": 30},
        {"hour": 14, "unaccounted": True},
    ]
    r = client.put(
        "/api/v1/users/alice/config/allowed-hours/all", json=hours, headers=AUTH
    )
    assert r.status_code == 200
    expected = {
        "7": {"STARTMIN": 0, "ENDMIN": 60, "UACC": 0},
        "11": {"STARTMIN": 0, "ENDMIN": 30, "UACC": 0},
        "14": {"STARTMIN": 0, "ENDMIN": 60, "UACC": 1},
    }
    assert fake.calls[-1] == ("setAllowedHours", ("alice", "ALL", expected))
    assert r.json()["allowed_hours"]["3"] == [
        {"hour": 7, "start_minute": 0, "end_minute": 60, "unaccounted": False},
        {"hour": 11, "start_minute": 0, "end_minute": 30, "unaccounted": False},
        {"hour": 14, "start_minute": 0, "end_minute": 60, "unaccounted": True},
    ]
    assert (
        client.put(
            "/api/v1/users/alice/config/allowed-hours/8", json=[], headers=AUTH
        ).status_code
        == 400
    )
    bad = [{"hour": 7, "start_minute": 30, "end_minute": 10}]
    r = client.put("/api/v1/users/alice/config/allowed-hours/1", json=bad, headers=AUTH)
    assert r.status_code == 400 and "start_minute" in r.text


def test_a_daemon_refusal_applies_nothing(client):
    before = client.get("/api/v1/users/alice/config", headers=AUTH).json()
    body = {"track_inactive": False, "limit_per_week": 13, "hide_tray_icon": True}
    r = client.patch("/api/v1/users/alice/config", json=body, headers=AUTH)
    assert r.status_code == 400
    assert "applied" not in r.json()
    assert r.json()["errors"] == [{"field": "limit_per_week", "message": "unlucky"}]
    r = client.patch(
        "/api/v1/users/alice/config",
        json={"allowed_days": [1], "limit_per_week": 13},
        headers=AUTH,
    )
    assert r.status_code == 400
    assert "applied" not in r.json()
    # neither the days nor the settings sent along were written
    assert client.get("/api/v1/users/alice/config", headers=AUTH).json() == before
    r = client.patch("/api/v1/users/alice/config", json={"bogus": 1}, headers=AUTH)
    assert r.status_code == 400 and r.json()["errors"][0]["field"] == "bogus"


def test_time_left(client, fake):
    r = client.post(
        "/api/v1/users/alice/time-left",
        json={"operation": "add", "seconds": 300},
        headers=AUTH,
    )
    assert r.status_code == 200 and r.json()["session_active"] is True
    assert fake.calls[-1] == ("setTimeLeft", ("alice", "+", 300))
    r = client.post(
        "/api/v1/users/alice/time-left",
        json={"operation": "give", "seconds": 1},
        headers=AUTH,
    )
    assert r.status_code == 400


def test_server_config(client, fake):
    config = client.get("/api/v1/config", headers=AUTH).json()
    assert (
        config["session_types_tracked"] == ["x11", "wayland"]
        and config["poll_time"] == 3
    )
    patch = {"poll_time": 5, "users_excluded": ["root", "nobody"]}
    r = client.patch("/api/v1/config", json=patch, headers=AUTH)
    assert r.status_code == 200 and r.json()["poll_time"] == 5
    assert fake.calls[-1] == ("setTimekprUsersExcl", (["root", "nobody"],))
    assert (
        client.patch("/api/v1/config", json={"poll_time": 0}, headers=AUTH).status_code
        == 400
    )
    # the daemon failed to apply a valid request
    r = client.patch("/api/v1/config", json={"log_level": 2}, headers=AUTH)
    assert r.status_code == 500 and r.json()["errors"][0]["field"] == "log_level"


def test_group_list(client):
    groups = client.get("/api/v1/groups", headers=AUTH).json()
    assert groups == [
        {"group": "all", "overrides": [], "members": []},
        {
            "group": "kids",
            "overrides": ["all"],
            "members": ["alice", "bob@idm.nixos.test"],
        },
    ]


def test_group_shapes(client):
    assert client.get("/api/v1/groups/teens", headers=AUTH).status_code == 404
    assert client.get("/api/v1/groups/teens/config", headers=AUTH).status_code == 404
    kids = client.get("/api/v1/groups/kids", headers=AUTH).json()
    assert kids["group"] == "kids" and "status" not in kids
    config = kids["config"]
    assert config["overrides"] == ["all"] and "hide_tray_icon" not in config
    assert config["limits_per_day"] == ALL_DAYS
    assert config == client.get("/api/v1/groups/kids/config", headers=AUTH).json()
    paths = client.get("/api/v1/openapi.json").json()["paths"]
    assert "/api/v1/groups/{group}/config/allowed-hours/{day}" in paths
    assert "/api/v1/groups/{group}/policy" in paths


def test_group_patch(client, fake):
    patch = {
        "allowed_days": [6, 7],
        "limits_per_day": {"6": 3600},
        "limit_per_week": 7200,
        "overrides": ["all", "guests"],
    }
    r = client.patch("/api/v1/groups/kids/config", json=patch, headers=AUTH)
    assert r.status_code == 200, r.text
    assert fake.calls == [
        (
            "applyPolicyChanges",
            (
                "@kids",
                [],
                {
                    "allowed_days": ["6", "7"],
                    "limits_per_day": {"6": 3600},
                    "limit_per_week": 7200,
                    "overrides": ["all", "guests"],
                },
            ),
        ),
    ]
    config = r.json()
    assert config["allowed_days"] == [6, 7]
    assert config["limits_per_day"] == {"6": 3600, "7": 0}
    assert config["overrides"] == ["all", "guests"]
    # clearing the overrides
    r = client.patch("/api/v1/groups/kids/config", json={"overrides": []}, headers=AUTH)
    assert r.status_code == 200 and r.json()["overrides"] == []
    # a group policy has no tray icon
    r = client.patch(
        "/api/v1/groups/kids/config", json={"hide_tray_icon": True}, headers=AUTH
    )
    assert r.status_code == 400 and r.json()["errors"][0]["field"] == "hide_tray_icon"
    # the first setter creates a policy for a group without one
    fake.calls.clear()
    r = client.patch(
        "/api/v1/groups/teens/config", json={"limits_per_day": {"1": 60}}, headers=AUTH
    )
    assert r.status_code == 200, r.text
    assert fake.calls == [
        ("applyPolicyChanges", ("@teens", [], {"limits_per_day": {"1": 60}})),
    ]
    # the other days keep the defaults of a new policy
    assert r.json()["limits_per_day"] == {
        "1": 60,
        **{str(d): 86400 for d in range(2, 8)},
    }
    assert "teens" in [
        g["group"] for g in client.get("/api/v1/groups", headers=AUTH).json()
    ]
    hours = [{"hour": 7}]
    r = client.put(
        "/api/v1/groups/guests/config/allowed-hours/all", json=hours, headers=AUTH
    )
    assert r.status_code == 200 and r.json()["allowed_hours"]["7"] == [
        {"hour": 7, "start_minute": 0, "end_minute": 60, "unaccounted": False}
    ]


def test_empty_patch_creates_group_policy(client, fake):
    """The web UI creates a group's policy with an empty PATCH"""
    r = client.patch("/api/v1/groups/teens/config", json={}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["overrides"] == []
    assert ("applyPolicyChanges", ("@teens", [], {})) in fake.calls
    assert "teens" in [
        g["group"] for g in client.get("/api/v1/groups", headers=AUTH).json()
    ]


def test_delete_policy(client, fake):
    r = client.delete("/api/v1/groups/kids/policy", headers=AUTH)
    assert r.status_code == 204 and r.content == b""
    assert fake.calls[-1] == ("deletePolicy", ("@kids",))
    r = client.delete("/api/v1/groups/kids/policy", headers=AUTH)
    assert r.status_code == 404 and r.headers["content-type"].startswith(PROBLEM)
    assert client.get("/api/v1/groups/kids", headers=AUTH).status_code == 404
    # a user's policy: the groups apply again, the user is still listed
    assert client.delete("/api/v1/users/alice/policy", headers=AUTH).status_code == 204
    assert client.delete("/api/v1/users/alice/policy", headers=AUTH).status_code == 404
    alice = client.get("/api/v1/users/alice", headers=AUTH).json()
    assert alice["policy_source"] == "default"
    assert (
        client.delete(
            "/api/v1/users/bob%40idm.nixos.test/policy", headers=AUTH
        ).status_code
        == 404
    )


def test_policy_settings_and_unset(client, fake):
    # what a policy holds is reported
    alice = client.get("/api/v1/users/alice", headers=AUTH).json()
    assert alice["policy_settings"] == ["limit_per_week", "track_inactive"]
    kids = client.get("/api/v1/groups/kids", headers=AUTH).json()
    assert kids["policy_settings"] == ["allowed_days", "limits_per_day", "overrides"]
    # a setter adds to it
    r = client.patch(
        "/api/v1/users/alice/config", json={"track_inactive": True}, headers=AUTH
    )
    assert r.status_code == 200
    alice = client.get("/api/v1/users/alice", headers=AUTH).json()
    assert alice["policy_settings"] == ["limit_per_week", "track_inactive"]
    # a null field takes the setting out of the policy
    r = client.patch(
        "/api/v1/users/alice/config", json={"track_inactive": None}, headers=AUTH
    )
    assert r.status_code == 200
    assert fake.calls[-1] == ("applyPolicyChanges", ("alice", ["track_inactive"], {}))
    # the same setting twice is one; a null and a value can mix, and the
    # value is not written when the null is refused
    r = client.patch(
        "/api/v1/users/alice/config",
        json={"allowed_days": None, "limits_per_day": None, "limit_per_month": 60},
        headers=AUTH,
    )
    assert r.status_code == 404, r.json()
    assert r.json()["detail"].endswith("does not set allowed_days")
    assert r.json()["errors"][0]["field"] == "allowed_days"
    assert fake.calls[-1] == (
        "applyPolicyChanges",
        ("alice", ["allowed_days"], {"limit_per_month": 60}),
    )
    config = client.get("/api/v1/users/alice/config", headers=AUTH).json()
    assert config["limit_per_month"] == 2678400
    # hours go by day, or all at once
    r = client.put("/api/v1/users/alice/config/allowed-hours/3", json=[], headers=AUTH)
    assert r.status_code == 200
    r = client.delete("/api/v1/users/alice/config/allowed-hours/3", headers=AUTH)
    assert r.status_code == 200
    assert fake.calls[-1] == ("unsetSetting", ("alice", "allowed_hours_3"))
    assert r.json()["allowed_hours"]["3"][0]["hour"] == 0
    r = client.delete("/api/v1/users/alice/config/allowed-hours/all", headers=AUTH)
    assert r.status_code == 404
    assert fake.calls[-1] == ("unsetSetting", ("alice", "allowed_hours"))
    r = client.delete("/api/v1/users/alice/config/allowed-hours/8", headers=AUTH)
    assert r.status_code == 400
    # the tray icon is not a group's setting, overrides not a user's
    r = client.patch(
        "/api/v1/groups/kids/config", json={"overrides": None}, headers=AUTH
    )
    assert r.status_code == 200
    assert fake.calls[-1] == ("applyPolicyChanges", ("@kids", ["overrides"], {}))
    assert client.get("/api/v1/groups/kids", headers=AUTH).json()[
        "policy_settings"
    ] == ["allowed_days", "limits_per_day"]
    r = client.patch(
        "/api/v1/groups/kids/config", json={"hide_tray_icon": None}, headers=AUTH
    )
    assert r.status_code == 400
    # the last setting of a user policy takes the policy with it
    r = client.patch(
        "/api/v1/users/alice/config", json={"limit_per_week": None}, headers=AUTH
    )
    assert r.status_code == 200
    alice = client.get("/api/v1/users/alice", headers=AUTH).json()
    assert alice["policy_source"] == "default" and alice["policy_settings"] == []


def test_migrate_policies(client, fake):
    r = client.post("/api/v1/policies/migrate", json={}, headers=AUTH)
    assert r.status_code == 200 and r.json() == {"users": ["carol"]}
    assert fake.calls[-1] == ("migratePolicies", (True,))
    r = client.post("/api/v1/policies/migrate", json={"dry_run": False}, headers=AUTH)
    assert r.status_code == 200 and r.json() == {"users": ["carol"]}
    assert fake.calls[-1] == ("migratePolicies", (False,))
    r = client.post("/api/v1/policies/migrate", json={"bogus": 1}, headers=AUTH)
    assert r.status_code == 400


def test_static_ui_and_openapi(client):
    r = client.get("/")
    assert r.status_code == 200 and "<title>timekpr</title>" in r.text
    assert client.get("/app.js").status_code == 200
    paths = client.get("/api/v1/openapi.json").json()["paths"]
    assert "/api/v1/users/{username}/config/allowed-hours/{day}" in paths
    r = client.get("/api/v1/nope", headers=AUTH)
    assert r.status_code == 404 and r.headers["content-type"].startswith(PROBLEM)


def test_no_auth_mode(fake):
    client = TestClient(create_app(Bridge(fake), token=None))
    assert client.get("/api/v1/users").status_code == 200


def test_host_check(fake):
    hosts = {"localhost", "127.0.0.1", "::1", "timekpr.example"}
    client = TestClient(create_app(Bridge(fake), token=None, allowed_hosts=hosts))
    r = client.get("/api/v1/users")
    assert r.status_code == 421 and r.headers["content-type"].startswith(PROBLEM)
    for host in ("localhost", "127.0.0.1:8463", "[::1]:8463", "TimeKpr.Example"):
        assert client.get("/api/v1/users", headers={"Host": host}).status_code == 200, (
            host
        )


def test_trust_is_per_socket():
    trusted = {"/run/timekprw/timekprw.sock"}
    assert is_trusted({"server": ("/run/timekprw/timekprw.sock", None)}, trusted)
    assert not is_trusted({"server": ("/tmp/other.sock", None)}, trusted)
    assert not is_trusted({"server": ("127.0.0.1", 8463)}, trusted)
    assert not is_trusted({"server": None}, trusted)
    assert not is_trusted({}, trusted)
    # the TestClient's connection is TCP as far as the app can tell
    client = TestClient(
        create_app(Bridge(FakeConnector()), token=TOKEN, trusted_sockets=trusted)
    )
    assert client.get("/api/v1/users").status_code == 401


def test_health_is_cached(fake):
    client = TestClient(create_app(Bridge(fake), token=None))
    for _ in range(5):
        assert client.get("/api/v1/health").status_code == 200
    assert fake.user_list_calls == 1


def test_arguments(monkeypatch):
    args = timekprw.parse_args(["--listen", "unix:/x", "--listen", "[::1]:1"])
    assert args.listen == ["unix:/x", "[::1]:1"]
    assert timekprw.parse_args([]).listen is None
    monkeypatch.setenv("TIMEKPRW_NO_AUTH", "false")
    assert timekprw.parse_args([]).no_auth is False
    monkeypatch.setenv("TIMEKPRW_NO_AUTH", "yes")
    assert timekprw.parse_args([]).no_auth is True
    monkeypatch.setenv("TIMEKPRW_NO_AUTH", "maybe")
    with pytest.raises(SystemExit):
        timekprw.parse_args([])
    assert timekprw.hostname("[::1]:8463") == "::1"
    assert timekprw.hostname("Example.org:80") == "example.org"
