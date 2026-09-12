"""timekprw's listeners, socket activation, and timekpra's HTTP connector,
against a real uvicorn started through timekprw.main()."""

import os
import socket
import stat
import subprocess
import sys
import time

import pytest
from timekpr.client.interface.http.administration import timekprAdminHttpConnector
from timekpr.common.constants import constants as cons
from timekpr.common.utils import webapi

HERE = os.path.dirname(os.path.abspath(__file__))
SERVE = os.path.join(HERE, "serve.py")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(server, connector, attempts=100):
    for _ in range(attempts):
        if server.process.poll() is not None:
            raise AssertionError("timekprw exited:\n" + server.read_log())
        connector.initTimekprConnection(True)
        if connector.isConnected()[0]:
            return
        time.sleep(0.1)
    raise AssertionError("timekprw did not come up:\n" + server.read_log())


class Server:
    """timekprw --listen ... in a subprocess, optionally socket activated
    (serve.py presents the given sockets the way systemd does)."""

    def __init__(self, tmp_path, args, activated=()):
        self.log_path = tmp_path / "timekprw.log"
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
        if activated:
            env["TIMEKPRW_TEST_ACTIVATION_FDS"] = ",".join(
                str(sock.fileno()) for sock in activated
            )
        with open(self.log_path, "w") as log:
            self.process = subprocess.Popen(
                [sys.executable, SERVE] + list(args),
                env=env,
                pass_fds=[sock.fileno() for sock in activated],
                stdout=log,
                stderr=subprocess.STDOUT,
            )

    def read_log(self):
        return self.log_path.read_text()

    def stop(self):
        self.process.terminate()
        self.process.wait()


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
        via_unix = timekprAdminHttpConnector("unix://" + unix_path, timeout=2)
        wait_for(server, via_unix)
        assert via_unix.isConnected() == (True, True)
        result, _message, users = via_unix.getUserList()
        assert (result, users) == (0, [["alice", "Alice"], ["bob@idm.nixos.test", ""]])

        # the daemon's shapes and key order, as timekpra prints them
        result, _message, info = via_unix.getUserConfigurationAndInformation(
            "alice", cons.TK_CL_INF_FULL
        )
        assert result == 0
        assert list(info)[:9] == [f"ALLOWED_HOURS_{day}" for day in range(1, 8)] + [
            "ALLOWED_WEEKDAYS",
            "LIMITS_PER_WEEKDAYS",
        ]
        assert info["ACTUAL_TIME_LEFT_DAY"] == 14 and info["TIME_SPENT_WEEK"] == 3
        assert "WAKEUP_HOUR_INTERVAL" not in info
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
        assert via_unix.setLockoutType("alice", "suspendwake", "7", "18") == (0, "")
        assert (
            via_unix.getUserConfigurationAndInformation("alice", "F")[2][
                "WAKEUP_HOUR_INTERVAL"
            ]
            == "7;18"
        )
        assert via_unix.setTimeLeft("alice", "+", 300) == (0, "")
        assert via_unix.setPlayTimeLeft("alice", "=", 0) == (0, "")
        assert via_unix.setTrackInactive("alice", True) == (0, "")
        assert via_unix.setPlayTimeEnabled("alice", True) == (0, "")
        assert via_unix.setPlayTimeActivities("alice", [["a", "b"], ["c", ""]]) == (
            0,
            "",
        )
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

        # TCP through the same server needs the token
        anonymous = timekprAdminHttpConnector(f"http://127.0.0.1:{port}", timeout=2)
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
        via_unix = timekprAdminHttpConnector("unix:" + unix_path, timeout=2)
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
        anonymous = timekprAdminHttpConnector("unix:" + unix_path, timeout=2)
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
    dead = timekprAdminHttpConnector("http://127.0.0.1:1", timeout=2)
    dead.initTimekprConnection(True)
    assert dead.isConnected() == (False, False)
