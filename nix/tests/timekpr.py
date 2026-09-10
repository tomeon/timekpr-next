# Test script for the timekpr NixOS test.  See timekpr.nix for the machine
# configuration and the surrounding documentation.
#
# The NixOS test driver runs this with `machine` and `subtest` in scope.
# timekpr.nix prepends a definition of CONFIG, a dict holding the values
# (user names, passwords, the timekpr store path) that both sides need.

import shlex

ALICE = CONFIG["alice"]
ALICE_PASSWORD = CONFIG["alicePassword"]
BOB = CONFIG["bob"]
BOB_PASSWORD = CONFIG["bobPassword"]
IDM_ADMIN_PASSWORD = CONFIG["idmAdminPassword"]
TIMEKPR_PACKAGE = CONFIG["timekprPackage"]
BASH = "/run/current-system/sw/bin/bash"

# How long each SSH login keeps its session open.  timekpr polls every
# 3 seconds and terminates an over-limit session after a 15 second
# countdown, so this leaves plenty of margin either way.
HOLD = 45
# Per-weekday limits (Mon..Sun) in seconds: no screen time at all.
NO_TIME = "0;0;0;0;0;0;0"
# Extra time granted for today, in seconds.
EXTRA_TIME = "300"
TIMEKPR_LOG = "/var/log/timekpr.log"


def timekpra(*args):
    return machine.succeed("timekpra " + " ".join(map(shlex.quote, args)))


def ssh_login(user, password):
    """Log in over SSH with a password, keep the session open for HOLD
    seconds, and return the command's (status, output)."""
    remote = f"sleep {HOLD}; echo SURVIVED"
    cmd = " ".join([
        "sshpass", "-p", shlex.quote(password),
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-l", shlex.quote(user), "localhost",
        shlex.quote(remote),
    ])
    return machine.execute(cmd, timeout=HOLD + 300)


def wait_until_logged_out(user):
    machine.wait_until_fails(
        f"loginctl list-users --no-legend | grep -F {shlex.quote(user)}"
    )
    machine.wait_until_succeeds(
        f"grep -F 'user \"{user}\" has gone' {TIMEKPR_LOG}"
    )


def expect_login_survives(user, password):
    status, out = ssh_login(user, password)
    assert status == 0 and "SURVIVED" in out, (
        f"login as {user} was cut short: status={status}, output={out!r}"
    )
    wait_until_logged_out(user)


def expect_login_terminated(user, password):
    status, out = ssh_login(user, password)
    assert status != 0 and "SURVIVED" not in out, (
        f"login as {user} was not terminated: status={status}, output={out!r}"
    )
    # timekpr flushes its log file lazily, so wait for the line.
    machine.wait_until_succeeds(
        f"grep -F 'killing \"{user}\" session' {TIMEKPR_LOG}"
    )
    wait_until_logged_out(user)


def exercise(user, password):
    with subtest(f"{user}: an unrestricted login survives"):
        expect_login_survives(user, password)

    with subtest(f"{user}: timekpr knows the user"):
        assert user in timekpra("--userlist")

    with subtest(f"{user}: forbid all screen time"):
        timekpra("--settimelimits", user, NO_TIME)

    with subtest(f"{user}: a restricted login is terminated"):
        expect_login_terminated(user, password)

    with subtest(f"{user}: grant {EXTRA_TIME} seconds of extra time"):
        timekpra("--settimeleft", user, "+", EXTRA_TIME)

    with subtest(f"{user}: an exempted login survives"):
        expect_login_survives(user, password)


def main():
    machine.wait_for_unit("multi-user.target")
    machine.wait_for_unit("timekpr.service")
    machine.wait_for_open_port(22)

    with subtest("timekpr runs this flake's package"):
        assert TIMEKPR_PACKAGE in machine.succeed(
            "systemctl show -p ExecStart --value timekpr.service"
        )

    exercise(ALICE, ALICE_PASSWORD)

    with subtest("kanidm: server, provisioning, and UNIX daemon are up"):
        machine.wait_for_unit("kanidm.service")
        machine.wait_for_unit("kanidm-unixd.service")
        machine.wait_for_file("/run/kanidm-unixd/sock")
        machine.wait_until_succeeds("kanidm-unix status | grep -q online")

    with subtest("kanidm: make bob a POSIX user with a UNIX password"):
        machine.succeed(
            f"answer-password {shlex.quote(IDM_ADMIN_PASSWORD)}"
            " kanidm login -D idm_admin"
        )
        machine.succeed("kanidm group posix set --gidnumber 10000 posix_users")
        machine.succeed(
            f"kanidm person posix set --gidnumber 10001 --shell {BASH} bob"
        )
        machine.succeed(
            f"answer-password {shlex.quote(BOB_PASSWORD)}"
            " kanidm person posix set-password bob"
        )
        machine.wait_until_succeeds(f"getent passwd {shlex.quote(BOB)}")

    exercise(BOB, BOB_PASSWORD)


machine.start()
try:
    main()
except Exception:
    # timekpr buffers its log and flushes it on shutdown; make it
    # available for diagnosing the failure.
    machine.execute("systemctl stop timekpr.service")
    print(machine.execute(f"cat {TIMEKPR_LOG}")[1])
    raise
