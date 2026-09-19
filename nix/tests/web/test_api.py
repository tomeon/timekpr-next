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
    assert fake.calls[-1] == ("setTimeLimitForDays", ("alice", [0] * 7))
    fake.calls.clear()
    patch = {"allowed_days": [4, 2], "limits_per_day": {"4": 3600}}
    r = client.patch("/api/v1/users/alice/config", json=patch, headers=AUTH)
    assert r.status_code == 200
    assert fake.calls == [
        ("setAllowedDays", ("alice", ["2", "4"])),
        ("setTimeLimitForDays", ("alice", [0, 3600])),
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


def test_daemon_refusal_reports_applied_fields(client):
    body = {"track_inactive": True, "limit_per_week": 13, "hide_tray_icon": True}
    r = client.patch("/api/v1/users/alice/config", json=body, headers=AUTH)
    assert r.status_code == 400
    assert r.json().get("applied", []) == []
    assert r.json()["errors"] == [{"field": "limit_per_week", "message": "unlucky"}]
    r = client.patch(
        "/api/v1/users/alice/config",
        json={"allowed_days": [1], "limit_per_week": 13},
        headers=AUTH,
    )
    assert r.status_code == 400
    assert r.json()["applied"] == ["allowed_days", "limits_per_day"]
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
