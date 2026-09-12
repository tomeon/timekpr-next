"""Shared pieces for the tests that run timekprw for real: a free port,
a timekprw subprocess on a fake daemon connector, and waiting for it."""

import os
import socket
import subprocess
import sys
import time

import timekpr.web

HERE = os.path.dirname(os.path.abspath(__file__))
SERVE = os.path.join(HERE, "serve.py")
# the UI as in the source tree (timekprw's default is the installed copy)
STATIC = os.path.join(os.path.dirname(timekpr.web.__file__), "static")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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


def wait_for(server, connector, attempts=100):
    """Wait until the connector reaches the server, failing fast if the
    server exited."""
    for _ in range(attempts):
        if server.process.poll() is not None:
            raise AssertionError("timekprw exited:\n" + server.read_log())
        connector.initTimekprConnection(True)
        if connector.isConnected()[0]:
            return
        time.sleep(0.1)
    raise AssertionError("timekprw did not come up:\n" + server.read_log())
