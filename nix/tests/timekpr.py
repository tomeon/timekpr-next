# Test script for the timekpr NixOS test.  See timekpr.nix for the machine
# configuration and the surrounding documentation.
#
# The NixOS test driver runs this with `machine` and `subtest` in scope.
# timekpr.nix prepends a definition of CONFIG, a dict holding the values
# (user names, passwords, the timekpr store path) that both sides need.
# Those three names are therefore undefined as far as linters can tell.
# ruff: noqa: F821

import json
import shlex
from urllib.parse import quote

ALICE = CONFIG["alice"]
ALICE_PASSWORD = CONFIG["alicePassword"]
BOB = CONFIG["bob"]
BOB_PASSWORD = CONFIG["bobPassword"]
CAROL = CONFIG["carol"]
DAVE = CONFIG["dave"]
ERIN = CONFIG["erin"]
# A group with a timekpr policy; alice is a member locally, bob through a
# Kanidm group of the same name (which NSS may present under another name,
# see bob_kids_group).
KIDS = CONFIG["kids"]
TIMEKPR_PACKAGE = CONFIG["timekprPackage"]
TIMEKPRW_TOKEN = CONFIG["timekprwToken"]
TIMEKPRW_TOKEN_FILE = CONFIG["timekprwTokenFile"]
TIMEKPRW_PORT = CONFIG["timekprwPort"]
TIMEKPRW_SOCKET = CONFIG["timekprwSocket"]
TIMEKPRW_URL = f"http://127.0.0.1:{TIMEKPRW_PORT}"
TIMEKPRW_UNIX_URL = f"unix://{TIMEKPRW_SOCKET}"
API = f"{TIMEKPRW_URL}/api/v1"

# How long each SSH login keeps its session open.  timekpr polls every
# 3 seconds and terminates an over-limit session after a 15 second
# countdown, so this leaves plenty of margin either way.
HOLD = 45
# ISO weekdays, Monday to Sunday.
ALL_DAYS = list(range(1, 8))
# Per-weekday limits in seconds: no screen time at all.
NO_TIME = {day: 0 for day in ALL_DAYS}
# The same as timekpra --settimelimits takes it.
NO_TIME_ARG = ";".join(map(str, NO_TIME.values()))
# Extra time granted for today, in seconds.
EXTRA_TIME = 300
# Per-weekday limits a privileged user sets, distinguishable from NO_TIME.
SOME_TIME = "60;60;60;60;60;60;60"
# Per-weekday limits that let a login of HOLD seconds survive comfortably.
PLENTY_TIME = ";".join(["3600"] * len(ALL_DAYS))
# The pseudo-group every user belongs to, for a policy on everyone.
ALL_GROUP = "all"
# A name no user or group has: the daemon answers a default policy for any
# user name, so a made-up one is what makes the "not found" cases.
NOSUCHUSER = "nosuchuser"
NOSUCHGROUP = "nosuchgroup"
TIMEKPR_LOG = "/var/log/timekpr.log"
# What timekpra prints when the daemon refuses a command.  timekpra
# always exits 0, so this text is the only signal.
DENIED = "access denied"
TIMEKPR_BUS = "com.timekpr.server /com/timekpr/server"
# A bus address nothing listens on, to prove that help needs no daemon.
NO_BUS = "unix:path=/nonexistent"
# The options every command must answer.
HELP_OPTIONS = ("-h", "--help")
# Each command's help output must mention this.
HELP_NEEDLES = {
    "timekpra": "--settimelimits",
    "timekprc": "start the user client",
    "timekprd": "start the timekpr daemon",
}
# Help is answered before any other work, so it takes about as long as
# starting an interpreter.  Generous, so that it does not flake under
# emulation, and still short enough to catch a command that loads its whole
# stack first: doing that took timekprc more than twenty seconds here.
HELP_TIMEOUT = 30
LIMITS_INTERFACE = "com.timekpr.server.user.limits"
SESSION_ATTRIBUTES_INTERFACE = "com.timekpr.server.user.sessionattributes"
POLKIT_READ = "com.timekpr.server.admin.read"
POLKIT_USER_CONFIGURE = "com.timekpr.server.user.admin.configure"
POLKIT_USER_TIME_LEFT = "com.timekpr.server.user.admin.time-left"


def timekpra(*args, server=None, token_file=None):
    """Run timekpra, against timekprw at `server` instead of D-Bus if given."""
    options = []
    if server is not None:
        options += ["--server", server]
    if token_file is not None:
        options += ["--token-file", token_file]
    return machine.succeed(
        "timekpra " + " ".join(map(shlex.quote, options + list(args)))
    )


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


def info_value(info, key, what):
    """The value of one `KEY: value` line of timekpra --userinfo or
    --groupinfo output."""
    for line in info.splitlines():
        if line.startswith(f"{key}: "):
            return line.split(": ", 1)[1]
    raise AssertionError(f"no {key} in the configuration of {what}:\n{info}")


def weekday_limits(user):
    return DBUS.userinfo_value(user, "LIMITS_PER_WEEKDAYS")


def group_target(group):
    """How timekpra addresses a group wherever it takes a user name."""
    return f"@{group}"


def api(method, path, body=None, token=TIMEKPRW_TOKEN, unix=False):
    """Call the web API (over TCP, or the UNIX socket if `unix`); return
    the HTTP status and the decoded body."""
    cmd = ["curl", "-sS", "-w", "\n%{http_code}", "-X", method]
    if unix:
        cmd += ["--unix-socket", TIMEKPRW_SOCKET]
    if token is not None:
        cmd += ["-H", f"Authorization: Bearer {token}"]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "--data", json.dumps(body)]
    out = machine.succeed(" ".join(map(shlex.quote, cmd + [API + path])))
    body, status = out.rsplit("\n", 1)
    return int(status), (json.loads(body) if body else None)


def expect(method, path, body=None, status=200, token=TIMEKPRW_TOKEN, unix=False):
    got, data = api(method, path, body, token, unix)
    assert got == status, f"{method} {path}: expected HTTP {status}, got {got}: {data}"
    return data


def user_path(user, suffix):
    return f"/users/{quote(user, safe='')}{suffix}"


def group_path(group, suffix=""):
    return f"/groups/{quote(group, safe='')}{suffix}"


class Timekpra:
    """Administer timekpr through the CLI, talking D-Bus or, given a
    server URL, HTTP to timekprw."""

    def __init__(self, server=None, token_file=None):
        self.server = server
        self.token_file = token_file
        self.name = "timekpra" if server is None else f"timekpra --server {server}"

    def run(self, *args):
        return timekpra(*args, server=self.server, token_file=self.token_file)

    def knows(self, user):
        return user in self.run("--userlist")

    def userinfo_value(self, user, key):
        return info_value(self.run("--userinfo", user), key, user)

    def groupinfo_value(self, group, key):
        return info_value(self.run("--groupinfo", group), key, group_target(group))

    def policy_source(self, user):
        """Where the user's effective policy comes from: "user" (a policy
        of their own), "group" or "default"."""
        return self.userinfo_value(user, "POLICY_SOURCE")

    def forbid(self, target):
        """No screen time at all for a user or a group (@group)."""
        self.run("--settimelimits", target, NO_TIME_ARG)

    def grant(self, user, seconds):
        self.run("--settimeleft", user, "+", str(seconds))

    def reset_time(self, user):
        """Forget any extra time granted earlier: the user's balance for
        today becomes exactly today's limit under their effective policy.
        Extra time lives in the user's counters, which a deleted or changed
        policy does not touch."""
        self.run("--settimeleft", user, "=", "0")

    def delete_policy(self, target):
        self.run("--deletepolicy", target)

    def ensure_no_user_policy(self, user):
        """Leave the user to their group policies or the defaults."""
        if self.policy_source(user) == "user":
            self.delete_policy(user)


DBUS = Timekpra()
UNIX = Timekpra(server=TIMEKPRW_UNIX_URL)
HTTP = Timekpra(server=TIMEKPRW_URL, token_file=TIMEKPRW_TOKEN_FILE)


def config_lines(userinfo):
    """The configuration lines of `timekpra --userinfo` output, without
    the counters (which move with the clock)."""
    return [
        line
        for line in userinfo.splitlines()
        if not line.startswith(("TIME_", "ACTUAL_"))
    ]


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


def exercise(user, password, admin):
    """A user's own policy, made before the user ever logged in: nothing
    is created on login any more, and a setting creates the policy."""
    with subtest(f"{user}: {admin.name} knows the user"):
        # the list holds every user of the system, logged in or not
        assert admin.knows(user)

    with subtest(f"{user}: the defaults apply before any policy is made"):
        assert admin.policy_source(user) == "default"

    with subtest(f"{user}: forbid all screen time via {admin.name} before any login"):
        admin.forbid(user)
        assert admin.policy_source(user) == "user"

    with subtest(f"{user}: a restricted login is terminated"):
        expect_login_terminated(user, password)

    with subtest(f"{user}: grant {EXTRA_TIME} seconds of extra time via {admin.name}"):
        admin.grant(user, EXTRA_TIME)

    with subtest(f"{user}: an exempted login survives"):
        expect_login_survives(user, password)

    with subtest(f"{user}: deleting the policy via {admin.name} lifts the limits"):
        admin.delete_policy(user)
        assert admin.policy_source(user) == "default"
        expect_login_survives(user, password)


def exercise_help():
    """Help must work for whoever may execute the command, whatever the
    state of the system: no root, no timekpr group, no daemon, no bus, no
    environment at all.  It must also be answered before any other work,
    which is what keeps it inside HELP_TIMEOUT."""
    for command, needle in HELP_NEEDLES.items():
        for option in HELP_OPTIONS:
            with subtest(f"{command} {option}: help for an unprivileged user"):
                # env -i: no PATH, no HOME, no XDG_*, no DISPLAY, and a bus
                # address nothing listens on, so nothing can be relied upon
                out = machine.succeed(
                    f"timeout {HELP_TIMEOUT}"
                    f" runuser -u {shlex.quote(CAROL)} -- env -i"
                    f" DBUS_SYSTEM_BUS_ADDRESS={shlex.quote(NO_BUS)}"
                    f" {TIMEKPR_PACKAGE}/bin/{command} {option}"
                )
                assert needle in out, out

    with subtest("help writes nothing"):
        # help comes before the self-running check and before logging is set
        # up, so the pid and log files of the commands a user runs are not
        # created (timekprd's own /tmp/timekprd.pid belongs to the daemon)
        for command in ("timekpra", "timekprc"):
            machine.succeed(f"test ! -e /tmp/{command}.{CAROL}.pid")
            machine.succeed(f"test ! -e /tmp/{command}.{CAROL}.log")


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
        assert DENIED in timekpra_as(
            CAROL, "--settimeleft", CAROL, "+", str(EXTRA_TIME)
        )
        wait_for_log(f"polkit: NOT AUTHORIZED {POLKIT_USER_TIME_LEFT}", f"user={CAROL}")

    with subtest(f"{CAROL}: an unprivileged user may not list users"):
        out = timekpra_as(CAROL, "--userlist")
        assert DENIED in out and ALICE not in out, out
        wait_for_log(f"polkit: NOT AUTHORIZED {POLKIT_READ}", "method=getUserList")

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
        # alice has no policy at this point; this creates one
        assert DENIED not in timekpra_as(DAVE, "--settimelimits", ALICE, SOME_TIME)
        assert weekday_limits(ALICE) == SOME_TIME
        assert ALICE in timekpra_as(DAVE, "--userlist")
        wait_for_log(f"polkit: AUTHORIZED {POLKIT_USER_CONFIGURE}", f"user={ALICE}")
        wait_for_log(f"polkit: AUTHORIZED {POLKIT_READ}", "method=getUserList")

    with subtest(f"{ERIN}: a rule may allow one action for one user only"):
        assert DENIED not in timekpra_as(
            ERIN, "--settimeleft", ALICE, "+", str(EXTRA_TIME)
        )
        wait_for_log(f"polkit: AUTHORIZED {POLKIT_USER_TIME_LEFT}", f"user={ALICE}")
        assert DENIED in timekpra_as(ERIN, "--settimeleft", CAROL, "+", str(EXTRA_TIME))
        assert DENIED in timekpra_as(ERIN, "--settimelimits", ALICE, NO_TIME_ARG)
        assert DENIED in timekpra_as(ERIN, "--userinfo", ALICE)
        assert weekday_limits(ALICE) == SOME_TIME


def check_web_api():
    """The API and timekpra --server against timekpra's D-Bus view of the
    same configuration (alice has just been exercised over D-Bus)."""
    config_path = user_path(ALICE, "/config")

    with subtest("timekprw: socket activation, health, authentication, and the UI"):
        machine.wait_for_unit("timekprw.socket")
        assert expect("GET", "/health", token=None)["daemon"] == "ok"
        machine.wait_for_unit("timekprw.service")
        expect("GET", "/users", status=401, token=None)
        expect("GET", "/users", status=401, token="wrong")
        # the UNIX socket's permissions are its access control: no token
        expect("GET", "/users", token=None, unix=True)
        assert "<title>timekpr</title>" in machine.succeed(f"curl -fsS {TIMEKPRW_URL}/")

    with subtest("timekpra --server: the same answers as over D-Bus"):
        for remote in (UNIX, HTTP):
            assert remote.run("--userlist") == DBUS.run("--userlist"), remote.name
            for command in ("--userinfo", "--userinfort"):
                expected = config_lines(DBUS.run(command, ALICE))
                got = config_lines(remote.run(command, ALICE))
                assert got == expected, (
                    f"{remote.name} {command}:\n{got}\n!=\n{expected}"
                )
        bad = machine.succeed(
            f"timekpra --server {TIMEKPRW_UNIX_URL} --userinfo {NOSUCHUSER}"
        )
        assert f"no configuration for user {NOSUCHUSER}" in bad, bad

    with subtest("timekpra --server: settings arrive at the daemon"):
        UNIX.run("--setallowedhours", ALICE, "3", "7;11[0-30];!14")
        UNIX.run("--setalloweddays", ALICE, "2;4")
        HTTP.run("--settimelimits", ALICE, "0;3600")
        info = DBUS.run("--userinfo", ALICE)
        for line in (
            "ALLOWED_HOURS_3: 7;11[0-30];!14",
            "ALLOWED_WEEKDAYS: 2;4",
            "LIMITS_PER_WEEKDAYS: 0;3600",
        ):
            assert line in info, f"{line!r} not in timekpra --userinfo:\n{info}"
        # back to the defaults (keeping "no time"), through D-Bus
        DBUS.run("--setalloweddays", ALICE, ";".join(map(str, ALL_DAYS)))
        DBUS.run("--settimelimits", ALICE, NO_TIME_ARG)
        DBUS.run("--setallowedhours", ALICE, "ALL", ";".join(map(str, range(24))))
        assert config_lines(UNIX.run("--userinfo", ALICE)) == config_lines(
            DBUS.run("--userinfo", ALICE)
        )

    with subtest("timekprw: reports what timekpra set"):
        config = expect("GET", config_path)
        assert config["limits_per_day"] == {str(day): 0 for day in ALL_DAYS}, config
        status = expect("GET", user_path(ALICE, "/status"))
        assert not status["session_active"], status
        assert "LIMITS_PER_WEEKDAYS: 0;0;0;0;0;0;0" in timekpra("--userinfo", ALICE)

    with subtest("timekprw: settings round-trip through timekpra"):
        hours = [
            {"hour": 7},
            {"hour": 11, "start_minute": 0, "end_minute": 30},
            {"hour": 14, "unaccounted": True},
        ]
        config = expect("PUT", user_path(ALICE, "/config/allowed-hours/3"), hours)
        assert [entry["hour"] for entry in config["allowed_hours"]["3"]] == [7, 11, 14]
        patch = {
            "allowed_days": [4, 2],
            "limits_per_day": {"4": 3600},
        }
        config = expect("PATCH", config_path, patch)
        assert config["allowed_days"] == [2, 4], config
        assert config["limits_per_day"] == {"2": 0, "4": 3600}, config
        info = timekpra("--userinfo", ALICE)
        for line in (
            "ALLOWED_HOURS_3: 7;11[0-30];!14",
            "ALLOWED_WEEKDAYS: 2;4",
            "LIMITS_PER_WEEKDAYS: 0;3600",
        ):
            assert line in info, f"{line!r} not in timekpra --userinfo:\n{info}"
        # restore the defaults, keeping the "no time" limits
        restore = {
            "allowed_days": ALL_DAYS,
            "limits_per_day": NO_TIME,
        }
        expect("PATCH", config_path, restore)
        all_hours = [{"hour": hour} for hour in range(24)]
        expect("PUT", user_path(ALICE, "/config/allowed-hours/all"), all_hours)

    with subtest("timekprw: rejects bad requests as problem details"):
        expect("GET", user_path(NOSUCHUSER, "/config"), status=404)
        expect("DELETE", user_path(NOSUCHUSER, "/policy"), status=404)
        problem = expect("PATCH", config_path, {"limits_per_day": {"8": 1}}, status=400)
        assert problem["errors"][0]["field"].startswith("limits_per_day"), problem
        body = {"operation": "give", "seconds": 1}
        expect("POST", user_path(ALICE, "/time-left"), body, status=400)

    with subtest("timekprw: daemon settings"):
        config = expect("GET", "/config")
        assert "tty" in config["session_types_tracked"], config
        # The daemon writes its settings to /etc/timekpr, which the NixOS
        # module makes a read-only store path, so the daemon fails to
        # apply the change and the API reports that as a server error.
        patch = {"log_level": config["log_level"]}
        problem = expect("PATCH", "/config", patch, status=500)
        assert problem["errors"][0]["field"] == "log_level", problem


def bob_kids_group():
    """The name NSS gives the Kanidm group bob is in.  Kanidm's UNIX
    daemon most likely presents it as its SPN (kids@idm.nixos.test), so it
    is read from the system rather than assumed."""
    groups = machine.succeed(f"id -Gn {shlex.quote(BOB)}").split()
    matching = [group for group in groups if group.startswith(KIDS)]
    assert matching, f"{BOB} is in no group named like {KIDS}: {groups}"
    return matching[0]


def exercise_groups():
    """Group policies: a policy on a group applies to its members until a
    member gets a policy of their own; several matching groups merge to
    the most restrictive values unless one overrides the other.  Both
    users were exercised before, so both have counters (with extra time
    granted) and neither may have a policy of their own here."""
    bob_kids = bob_kids_group()
    # one policy per distinct group name (bob's may or may not be alice's)
    kids_groups = sorted({KIDS, bob_kids})
    all_target = group_target(ALL_GROUP)

    with subtest("groups: the users start without a policy of their own"):
        for user in (ALICE, BOB):
            DBUS.ensure_no_user_policy(user)

    with subtest(f"groups: forbid all screen time for {', '.join(kids_groups)}"):
        for group in kids_groups:
            DBUS.forbid(group_target(group))
        listing = DBUS.run("--grouplist")
        for group in kids_groups:
            assert group in listing.splitlines(), listing
        assert UNIX.run("--grouplist") == listing
        assert DBUS.groupinfo_value(KIDS, "LIMITS_PER_WEEKDAYS") == NO_TIME_ARG
        assert UNIX.run("--groupinfo", KIDS) == DBUS.run("--groupinfo", KIDS)
        assert DBUS.policy_source(ALICE) == "group"
        assert DBUS.userinfo_value(ALICE, "POLICY_GROUPS") == KIDS
        assert DBUS.userinfo_value(BOB, "POLICY_GROUPS") == bob_kids
        assert f"{ALICE}  (policy: group:{KIDS})" in DBUS.run("--userlist")
        # the extra time granted earlier would outlast the group policy
        for user in (ALICE, BOB):
            DBUS.reset_time(user)

    with subtest("groups: the members' logins are terminated"):
        expect_login_terminated(ALICE, ALICE_PASSWORD)
        expect_login_terminated(BOB, BOB_PASSWORD)

    with subtest(f"groups: {ALICE}'s own policy wins over the group's"):
        DBUS.run("--settimelimits", ALICE, PLENTY_TIME)
        assert DBUS.policy_source(ALICE) == "user"
        expect_login_survives(ALICE, ALICE_PASSWORD)
        expect_login_terminated(BOB, BOB_PASSWORD)
        DBUS.delete_policy(ALICE)
        assert DBUS.policy_source(ALICE) == "group"
        expect_login_terminated(ALICE, ALICE_PASSWORD)

    with subtest("groups: matching groups merge to the most restrictive values"):
        DBUS.run("--settimelimits", all_target, PLENTY_TIME)
        assert DBUS.userinfo_value(ALICE, "POLICY_GROUPS") == f"{ALL_GROUP};{KIDS}"
        assert weekday_limits(ALICE) == NO_TIME_ARG

    with subtest("groups: an override takes a group's policy out of the merge"):
        DBUS.run("--setoverrides", all_target, KIDS)
        assert DBUS.groupinfo_value(ALL_GROUP, "OVERRIDES") == KIDS
        assert DBUS.userinfo_value(ALICE, "POLICY_GROUPS") == ALL_GROUP
        assert weekday_limits(ALICE) == PLENTY_TIME
        expect_login_survives(ALICE, ALICE_PASSWORD)
        DBUS.run("--setoverrides", all_target, "")
        assert DBUS.groupinfo_value(ALL_GROUP, "OVERRIDES") == ""
        assert DBUS.userinfo_value(ALICE, "POLICY_GROUPS") == f"{ALL_GROUP};{KIDS}"
        expect_login_terminated(ALICE, ALICE_PASSWORD)

    with subtest("groups: nothing is left to migrate"):
        assert DBUS.run("--migratepolicies", "dry-run").strip() == ""

    with subtest("timekprw: group policies"):
        groups = expect("GET", "/groups")
        kids = next((entry for entry in groups if entry["group"] == KIDS), None)
        assert kids is not None and ALICE in kids["members"], groups
        config = expect("GET", group_path(KIDS, "/config"))
        assert config["limits_per_day"] == {str(day): 0 for day in ALL_DAYS}, config
        assert "hide_tray_icon" not in config, config
        config = expect(
            "PATCH", group_path(KIDS, "/config"), {"overrides": [ALL_GROUP]}
        )
        assert config["overrides"] == [ALL_GROUP], config
        assert DBUS.groupinfo_value(KIDS, "OVERRIDES") == ALL_GROUP
        assert DBUS.userinfo_value(ALICE, "POLICY_GROUPS") == KIDS
        expect("GET", group_path(NOSUCHGROUP, "/config"), status=404)
        expect("DELETE", group_path(ALL_GROUP, "/policy"), status=204)
        expect("DELETE", group_path(ALL_GROUP, "/policy"), status=404)
        expect("GET", group_path(ALL_GROUP), status=404)
        assert ALL_GROUP not in DBUS.run("--grouplist").splitlines()

    with subtest("groups: deleting the policies lifts the limits"):
        for group in kids_groups:
            DBUS.delete_policy(group_target(group))
        listing = DBUS.run("--grouplist")
        for group in kids_groups:
            assert group not in listing.splitlines(), listing
        assert DBUS.policy_source(ALICE) == "default"
        assert DBUS.policy_source(BOB) == "default"
        expect_login_survives(ALICE, ALICE_PASSWORD)
        expect_login_survives(BOB, BOB_PASSWORD)


def main():
    machine.wait_for_unit("multi-user.target")
    machine.wait_for_unit("timekpr.service")
    machine.wait_for_unit("timekprw.socket")
    machine.wait_for_open_port(22)
    machine.wait_for_open_port(TIMEKPRW_PORT)

    with subtest("timekpr runs this flake's package"):
        for unit in ("timekpr.service", "timekprw.service"):
            assert TIMEKPR_PACKAGE in machine.succeed(
                f"systemctl show -p ExecStart --value {unit}"
            )

    exercise_help()
    exercise(ALICE, ALICE_PASSWORD, DBUS)
    exercise_authorization()
    check_web_api()

    with subtest("kanidm: the demo machine gave bob a POSIX account"):
        machine.wait_for_unit("demo-kanidm-users.service")
        machine.wait_until_succeeds(f"getent passwd {shlex.quote(BOB)}")

    exercise(BOB, BOB_PASSWORD, UNIX)
    exercise_groups()


machine.start()
try:
    main()
except Exception:
    # timekpr buffers its log and flushes it on shutdown; make it
    # available for diagnosing the failure.
    machine.execute("systemctl stop timekpr.service")
    print(machine.execute(f"cat {TIMEKPR_LOG}")[1])
    print(machine.execute("journalctl -u timekprw.service")[1])
    raise
