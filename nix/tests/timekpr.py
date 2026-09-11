# Test script for the timekpr NixOS test.  See timekpr.nix for the machine
# configuration and the surrounding documentation.
#
# The NixOS test driver runs this with `machine` and `subtest` in scope.
# timekpr.nix prepends a definition of CONFIG, a dict holding the values
# (user names, passwords, the timekpr store path) that both sides need.
# Those three names are therefore undefined as far as linters can tell.
# ruff: noqa: F821

import shlex

ALICE = CONFIG["alice"]
ALICE_PASSWORD = CONFIG["alicePassword"]
BOB = CONFIG["bob"]
BOB_PASSWORD = CONFIG["bobPassword"]
IDM_ADMIN_PASSWORD = CONFIG["idmAdminPassword"]
CAROL = CONFIG["carol"]
DAVE = CONFIG["dave"]
ERIN = CONFIG["erin"]
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
# Per-weekday limits a privileged user sets, distinguishable from NO_TIME.
SOME_TIME = "60;60;60;60;60;60;60"
TIMEKPR_LOG = "/var/log/timekpr.log"
# What timekpra prints when the daemon refuses a command.  timekpra
# always exits 0, so this text is the only signal.
DENIED = "access denied"
TIMEKPR_BUS = "com.timekpr.server /com/timekpr/server"
LIMITS_INTERFACE = "com.timekpr.server.user.limits"
SESSION_ATTRIBUTES_INTERFACE = "com.timekpr.server.user.sessionattributes"
POLKIT_USER_CONFIGURE = "com.timekpr.server.user.admin.configure"
POLKIT_USER_TIME_LEFT = "com.timekpr.server.user.admin.time-left"


def timekpra(*args):
    return machine.succeed("timekpra " + " ".join(map(shlex.quote, args)))


def timekpra_as(user, *args):
    """Run timekpra as an unprivileged user and return its output."""
    return machine.succeed(
        f"runuser -u {shlex.quote(user)} -- timekpra "
        + " ".join(map(shlex.quote, args))
    )


def call_as(user, interface, method, signature, *args):
    """Call a method on the daemon as the given user; (status, output)."""
    return machine.execute(
        f"runuser -u {shlex.quote(user)} -- busctl --system call {TIMEKPR_BUS}"
        f" {interface} {method} {signature} " + " ".join(map(shlex.quote, args))
    )


def wait_for_log(*needles):
    """Wait for a log line containing all the needles; the log is flushed lazily."""
    first, *rest = (f"grep -F {shlex.quote(needle)}" for needle in needles)
    machine.wait_until_succeeds(" | ".join([f"{first} {TIMEKPR_LOG}", *rest]))


def weekday_limits(user):
    for line in timekpra("--userinfo", user).splitlines():
        if line.startswith("LIMITS_PER_WEEKDAYS: "):
            return line.split(": ", 1)[1]
    raise AssertionError(f"no LIMITS_PER_WEEKDAYS in the configuration of {user}")


def ssh_login(user, password):
    """Log in over SSH with a password, keep the session open for HOLD
    seconds, and return the command's (status, output)."""
    remote = f"sleep {HOLD}; echo SURVIVED"
    cmd = " ".join(
        [
            "sshpass",
            "-p",
            shlex.quote(password),
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-l",
            shlex.quote(user),
            "localhost",
            shlex.quote(remote),
        ]
    )
    return machine.execute(cmd, timeout=HOLD + 300)


def wait_until_logged_out(user):
    machine.wait_until_fails(
        f"loginctl list-users --no-legend | grep -F {shlex.quote(user)}"
    )
    machine.wait_until_succeeds(f"grep -F 'user \"{user}\" has gone' {TIMEKPR_LOG}")


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
    machine.wait_until_succeeds(f"grep -F 'killing \"{user}\" session' {TIMEKPR_LOG}")
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


def exercise_authorization():
    """The daemon decides who may call what: the admin interfaces go
    through polkit, the per-user interfaces are limited to the user in
    question.  Nobody here has an authentication agent, so anyone who
    would have to authenticate is refused outright."""
    with subtest(f"{CAROL}: an unprivileged user may not change limits"):
        before = weekday_limits(ALICE)
        assert DENIED in timekpra_as(CAROL, "--settimelimits", ALICE, SOME_TIME)
        assert weekday_limits(ALICE) == before
        wait_for_log(f"polkit: NOT AUTHORIZED {POLKIT_USER_CONFIGURE}", f"user={ALICE}")

    with subtest(f"{CAROL}: an unprivileged user may not grant themselves time"):
        assert DENIED in timekpra_as(CAROL, "--settimeleft", CAROL, "+", EXTRA_TIME)
        wait_for_log(f"polkit: NOT AUTHORIZED {POLKIT_USER_TIME_LEFT}", f"user={CAROL}")

    with subtest(f"{CAROL}: an unprivileged user may not list users"):
        out = timekpra_as(CAROL, "--userlist")
        assert DENIED in out and ALICE not in out, out

    with subtest(f"{CAROL}: the per-user interfaces refuse other users"):
        # busctl reports the AccessDenied error only on stderr, which the
        # driver does not capture, so check its status and the daemon's log.
        calls = [
            (LIMITS_INTERFACE, "requestTimeLeft", "s", ALICE),
            (LIMITS_INTERFACE, "requestTimeLimits", "s", ALICE),
            (
                SESSION_ATTRIBUTES_INTERFACE,
                "processUserSessionAttributes",
                "ssss",
                ALICE,
                "scrs",
                "",
                "true",
            ),
        ]
        for call in calls:
            status, out = call_as(CAROL, *call)
            assert status != 0, f"{call[1]} was not refused: {out!r}"
        wait_for_log("ACCESS DENIED", f'about user "{ALICE}"')

    with subtest(f"{ALICE}: the per-user interfaces accept the user themselves"):
        # alice is not logged in, so the daemon answers "not found";
        # the point is that the call is not refused.
        status, out = call_as(ALICE, LIMITS_INTERFACE, "requestTimeLeft", "s", ALICE)
        assert status == 0 and "is not found" in out, out

    with subtest(f"{DAVE}: a member of the timekpr group may change limits"):
        assert DENIED not in timekpra_as(DAVE, "--settimelimits", ALICE, SOME_TIME)
        assert weekday_limits(ALICE) == SOME_TIME
        assert ALICE in timekpra_as(DAVE, "--userlist")
        wait_for_log(f"polkit: AUTHORIZED {POLKIT_USER_CONFIGURE}", f"user={ALICE}")

    with subtest(f"{ERIN}: a rule may allow one action for one user only"):
        assert DENIED not in timekpra_as(ERIN, "--settimeleft", ALICE, "+", EXTRA_TIME)
        wait_for_log(f"polkit: AUTHORIZED {POLKIT_USER_TIME_LEFT}", f"user={ALICE}")
        assert DENIED in timekpra_as(ERIN, "--settimeleft", CAROL, "+", EXTRA_TIME)
        assert DENIED in timekpra_as(ERIN, "--settimelimits", ALICE, NO_TIME)
        assert weekday_limits(ALICE) == SOME_TIME


def main():
    machine.wait_for_unit("multi-user.target")
    machine.wait_for_unit("timekpr.service")
    machine.wait_for_open_port(22)

    with subtest("timekpr runs this flake's package"):
        assert TIMEKPR_PACKAGE in machine.succeed(
            "systemctl show -p ExecStart --value timekpr.service"
        )

    exercise(ALICE, ALICE_PASSWORD)
    exercise_authorization()

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
        machine.succeed(f"kanidm person posix set --gidnumber 10001 --shell {BASH} bob")
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
