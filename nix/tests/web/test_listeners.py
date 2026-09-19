"""timekprw's listeners, socket activation, and timekpra's HTTP connector,
against a real uvicorn started through timekprw.main()."""

import os
import socket
import stat

import pytest
from helpers import Server, free_port, wait_for

from timekpr.client.interface.http.administration import timekprAdminHttpConnector
from timekpr.common.constants import constants as cons
from timekpr.common.utils import webapi


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "token"
    path.write_text("secret\n")
    path.chmod(0o600)
    return str(path)


def test_socket_activation_and_connector(tmp_path, token_file):
    unix_path = str(tmp_path / "activated.sock")
    port = free_port()
    tcp = socket.socket()
    tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp.bind(("127.0.0.1", port))
    tcp.listen()
    unix = socket.socket(socket.AF_UNIX)
    unix.bind(unix_path)
    unix.listen()
    server = Server(
        tmp_path,
        ["--static-dir", "", "--token-file", token_file],
        activated=[tcp, unix],
    )
    try:
        via_unix = timekprAdminHttpConnector("unix://" + unix_path, timeout=10)
        wait_for(server, via_unix)
        assert via_unix.isConnected() == (True, True)
        result, _message, users = via_unix.getUserList()
        assert (result, users) == (
            0,
            [["alice", "Alice", "user"], ["bob@idm.nixos.test", "", "default"]],
        )

        # the daemon's shapes and key order, as timekpra prints them
        result, _message, info = via_unix.getUserConfigurationAndInformation(
            "alice", cons.TK_CL_INF_FULL
        )
        assert result == 0
        assert list(info)[:9] == [f"ALLOWED_HOURS_{day}" for day in range(1, 8)] + [
            "ALLOWED_WEEKDAYS",
            "LIMITS_PER_WEEKDAYS",
        ]
        assert list(info)[13:15] == ["POLICY_SOURCE", "POLICY_GROUPS"]
        assert info["POLICY_SOURCE"] == "user" and info["POLICY_GROUPS"] == []
        assert info["ACTUAL_TIME_LEFT_DAY"] == 14 and info["TIME_SPENT_WEEK"] == 3
        _, _, realtime = via_unix.getUserConfigurationAndInformation(
            "alice", cons.TK_CL_INF_RT
        )
        assert list(realtime) == list(webapi.STATUS_LIVE.values())
        _, _, realtime = via_unix.getUserConfigurationAndInformation(
            "bob@idm.nixos.test", cons.TK_CL_INF_RT
        )
        assert realtime == {}
        _, _, saved = via_unix.getUserConfigurationAndInformation(
            "bob@idm.nixos.test", cons.TK_CL_INF_SAVED
        )
        assert list(saved) == list(webapi.STATUS_SAVED.values())

        # setters, including the positional limits and the generated ones
        assert via_unix.setAllowedDays("alice", ["2", "4"]) == (0, "")
        assert via_unix.setTimeLimitForDays("alice", [100, 200, 300]) == (0, "")
        _, _, info = via_unix.getUserConfigurationAndInformation(
            "alice", cons.TK_CL_INF_FULL
        )
        assert (info["ALLOWED_WEEKDAYS"], info["LIMITS_PER_WEEKDAYS"]) == (
            ["2", "4"],
            [100, 200],
        )
        hours = {
            "7": {"STARTMIN": 0, "ENDMIN": 60, "UACC": False},
            "11": {"STARTMIN": 0, "ENDMIN": 30, "UACC": True},
        }
        assert via_unix.setAllowedHours("alice", "ALL", hours) == (0, "")
        _, _, info = via_unix.getUserConfigurationAndInformation(
            "alice", cons.TK_CL_INF_FULL
        )
        assert info["ALLOWED_HOURS_5"] == {
            "7": {"STARTMIN": 0, "ENDMIN": 60, "UACC": 0},
            "11": {"STARTMIN": 0, "ENDMIN": 30, "UACC": 1},
        }
        assert via_unix.setTimeLeft("alice", "+", 300) == (0, "")
        assert via_unix.setTrackInactive("alice", True) == (0, "")
        assert via_unix.setTimekprPollTime(9) == (0, "")
        result, _, config = via_unix.getTimekprConfiguration()
        assert result == 0 and config["TIMEKPR_POLLTIME"] == 9
        assert list(config) == [key for key, _setter in webapi.SERVER_FIELDS.values()]

        # the daemon's messages come through
        assert via_unix.setTimeLimitForWeek("alice", 13) == (-1, "unlucky")
        result, message = via_unix.setTimekprLogLevel(2)
        assert result == -1 and "Unexpected ERROR" in message
        result, message, info = via_unix.getUserConfigurationAndInformation(
            "nobody", "F"
        )
        assert result == -1 and "no configuration" in message and info == {}

        # groups and policies, as timekpra --grouplist and friends use them
        result, _, groups = via_unix.getGroupList()
        assert (result, groups) == (
            0,
            [["all", "", ""], ["kids", "all", "alice;bob@idm.nixos.test"]],
        )
        result, _, info = via_unix.getUserConfigurationAndInformation(
            "@kids", cons.TK_CL_INF_FULL
        )
        assert result == 0 and list(info)[9:] == [
            "TRACK_INACTIVE",
            "LIMIT_PER_WEEK",
            "LIMIT_PER_MONTH",
            "OVERRIDES",
        ]
        assert info["OVERRIDES"] == ["all"] and info["LIMITS_PER_WEEKDAYS"] == [0] * 7
        assert via_unix.setOverrides("@kids", ["@all", "guests"]) == (0, "")
        _, _, info = via_unix.getUserConfigurationAndInformation("@kids", "F")
        assert info["OVERRIDES"] == ["all", "guests"]
        # the group's first setting creates its policy
        assert via_unix.setTimeLimitForDays("@teens", [0] * 7) == (0, "")
        _, _, info = via_unix.getUserConfigurationAndInformation("@teens", "F")
        assert info["LIMITS_PER_WEEKDAYS"] == [0] * 7
        assert via_unix.setTrackInactive("@teens", True) == (0, "")
        # per-user settings for a group, and per-group settings for a user
        result, message = via_unix.setHideTrayIcon("@kids", True)
        assert result == -1 and "is a group" in message
        result, message = via_unix.setTimeLeft("@kids", "+", 1)
        assert result == -1 and "is a group" in message
        result, message = via_unix.setOverrides("alice", ["kids"])
        assert result == -1 and "is a user" in message
        assert via_unix.deletePolicy("@teens") == (0, "")
        result, message = via_unix.deletePolicy("@teens")
        assert result == -1 and "no policy" in message
        assert via_unix.deletePolicy("alice") == (0, "")
        _, _, info = via_unix.getUserConfigurationAndInformation("alice", "F")
        assert info["POLICY_SOURCE"] == "default"
        assert via_unix.migratePolicies(True) == (0, "", ["carol"])
        assert via_unix.migratePolicies(False) == (0, "", ["carol"])

        # TCP through the same server needs the token
        anonymous = timekprAdminHttpConnector(f"http://127.0.0.1:{port}", timeout=10)
        wait_for(server, anonymous)
        result, message, users = anonymous.getUserList()
        assert result == -1 and "bearer" in message and users == []
        with_token = timekprAdminHttpConnector(
            f"http://127.0.0.1:{port}", token_file, timeout=5
        )
        assert with_token.getUserList()[0] == 0
    finally:
        server.stop()
        tcp.close()
        unix.close()


def test_own_listeners(tmp_path, token_file):
    unix_path = str(tmp_path / "own.sock")
    port = free_port()
    args = [
        "--listen",
        "unix:" + unix_path,
        "--listen",
        f"127.0.0.1:{port}",
        "--token-file",
        token_file,
        "--static-dir",
        "",
    ]
    server = Server(tmp_path, args)
    try:
        via_unix = timekprAdminHttpConnector("unix:" + unix_path, timeout=10)
        wait_for(server, via_unix)
        assert stat.S_IMODE(os.stat(unix_path).st_mode) == 0o660
        assert via_unix.getUserList()[0] == 0
        via_tcp = timekprAdminHttpConnector(
            f"http://127.0.0.1:{port}", token_file, timeout=5
        )
        assert via_tcp.getUserList()[0] == 0
        # DNS rebinding: a foreign Host header is refused on TCP only
        foreign = timekprAdminHttpConnector(
            f"http://127.0.0.1:{port}", token_file, timeout=5
        )
        status, _body = foreign._request(
            "GET", "/users", headers={"Host": "evil.example"}
        )
        assert status == 421
        status, _body = via_unix._request(
            "GET", "/users", headers={"Host": "evil.example"}
        )
        assert status == 200
    finally:
        server.stop()


def test_auth_unix_requires_the_token(tmp_path, token_file):
    unix_path = str(tmp_path / "strict.sock")
    args = [
        "--listen",
        "unix:" + unix_path,
        "--auth-unix",
        "--token-file",
        token_file,
        "--static-dir",
        "",
    ]
    server = Server(tmp_path, args)
    try:
        anonymous = timekprAdminHttpConnector("unix:" + unix_path, timeout=10)
        wait_for(server, anonymous)
        assert anonymous.getUserList()[0] == -1
        with_token = timekprAdminHttpConnector(
            "unix:" + unix_path, token_file, timeout=5
        )
        assert with_token.getUserList()[0] == 0
    finally:
        server.stop()


def test_refuses_tcp_without_a_token(tmp_path):
    args = [
        "--listen",
        f"127.0.0.1:{free_port()}",
        "--token-file",
        str(tmp_path / "missing"),
        "--static-dir",
        "",
    ]
    server = Server(tmp_path, args)
    assert server.process.wait(timeout=30) != 0
    assert "no token file found" in server.read_log()


def test_unreachable_server_is_reported():
    dead = timekprAdminHttpConnector("http://127.0.0.1:1", timeout=10)
    dead.initTimekprConnection(True)
    assert dead.isConnected() == (False, False)
