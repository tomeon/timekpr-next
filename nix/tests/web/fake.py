"""A stand-in for timekprAdminConnector: the daemon's data shapes, the
daemon's (result, message[, payload]) convention, and just enough state
to read back what the setters wrote."""

import copy

import dbus

from timekpr.common.constants import constants as cons
from timekpr.common.constants import messages as msg

ALL_HOURS = {str(hour): {"STARTMIN": 0, "ENDMIN": 60, "UACC": 0} for hour in range(24)}
LIVE = {
    "ACTUAL_TIME_SPENT_SESSION": 10,
    "ACTUAL_TIME_INACTIVE_SESSION": 11,
    "ACTUAL_TIME_SPENT_BALANCE": 12,
    "ACTUAL_TIME_SPENT_DAY": 13,
    "ACTUAL_TIME_LEFT_DAY": 14,
    "ACTUAL_TIME_LEFT_CONTINUOUS": 15,
}
COUNTERS = {
    "TIME_SPENT_BALANCE": 1,
    "TIME_SPENT_DAY": 2,
    "TIME_SPENT_WEEK": 3,
    "TIME_SPENT_MONTH": 4,
    "TIME_LEFT_DAY": 5,
}
# the group with a policy, its overrides and its known members
GROUPS = {"all": ("", ""), "kids": ("all", "alice;bob@idm.nixos.test")}


def default_limits():
    """The limits part of a policy as the daemon returns it, with dbus
    types where the daemon uses them."""
    info = {
        f"ALLOWED_HOURS_{day}": dbus.Dictionary(ALL_HOURS, signature="sv")
        for day in range(1, 8)
    }
    info.update(
        {
            "ALLOWED_WEEKDAYS": [dbus.String(str(day)) for day in range(1, 8)],
            "LIMITS_PER_WEEKDAYS": [dbus.Int32(86400)] * 7,
            "TRACK_INACTIVE": dbus.Boolean(False),
            "HIDE_TRAY_ICON": False,
            "LIMIT_PER_WEEK": 604800,
            "LIMIT_PER_MONTH": 2678400,
        }
    )
    return info


def default_user():
    """A user's full information: the limits, where they come from, and
    the saved counters."""
    info = default_limits()
    info["POLICY_SOURCE"] = "default"
    info["POLICY_GROUPS"] = dbus.Array(signature="s")
    info["POLICY_SETTINGS"] = dbus.Array(signature="s")
    info.update(COUNTERS)
    return info


def default_group():
    """A group's policy: the limits and the groups it overrides, no counters."""
    info = default_limits()
    info["OVERRIDES"] = dbus.Array(signature="s")
    info["POLICY_SETTINGS"] = dbus.Array(signature="s")
    return info


# the daemon's key(s) behind each setting name, as the daemon unsets them
SETTING_KEYS = {
    "allowed_days": ("ALLOWED_WEEKDAYS", "LIMITS_PER_WEEKDAYS"),
    "limits_per_day": ("ALLOWED_WEEKDAYS", "LIMITS_PER_WEEKDAYS"),
    "allowed_hours": tuple(f"ALLOWED_HOURS_{day}" for day in range(1, 8)),
    **{f"allowed_hours_{day}": (f"ALLOWED_HOURS_{day}",) for day in range(1, 8)},
    "limit_per_week": ("LIMIT_PER_WEEK",),
    "limit_per_month": ("LIMIT_PER_MONTH",),
    "track_inactive": ("TRACK_INACTIVE",),
    "hide_tray_icon": ("HIDE_TRAY_ICON",),
    "overrides": ("OVERRIDES",),
}


def is_group(target):
    return target.startswith(cons.TK_GROUP_TARGET_PREFIX)


class FakeConnector:
    """alice has an active session and a policy of her own,
    bob@idm.nixos.test has neither.  The group kids has a policy that
    overrides the one of the pseudo-group all, and grants no time.  A
    week limit of 13 is refused with the daemon's -1 convention, and
    setting the log level fails the way the daemon fails on a read-only
    /etc/timekpr."""

    def __init__(self):
        # the setters called, in order; reads are counted separately
        self.calls = []
        self.user_list_calls = 0
        self.users = {"alice": default_user(), "bob@idm.nixos.test": default_user()}
        self.users["alice"]["POLICY_SOURCE"] = "user"
        # the policies that exist as files: users by name, groups as @group
        self.policies = {"alice", "@all", "@kids"}
        self.groups = {"@all": default_group(), "@kids": default_group()}
        self.groups["@kids"]["OVERRIDES"] = [dbus.String("all")]
        self.groups["@kids"]["LIMITS_PER_WEEKDAYS"] = [dbus.Int32(0)] * 7
        self.groups["@kids"]["POLICY_SETTINGS"] = [
            dbus.String(name)
            for name in ("allowed_days", "limits_per_day", "overrides")
        ]
        self.users["alice"]["POLICY_SETTINGS"] = [
            dbus.String("limit_per_week"),
            dbus.String("track_inactive"),
        ]
        self.users["alice"]["TRACK_INACTIVE"] = dbus.Boolean(True)
        self.server = {
            "TIMEKPR_LOGLEVEL": 1,
            "TIMEKPR_POLLTIME": 3,
            "TIMEKPR_SAVE_TIME": 30,
            "TIMEKPR_TERMINATION_TIME": 15,
            "TIMEKPR_FINAL_WARNING_TIME": 60,
            "TIMEKPR_FINAL_NOTIFICATION_TIME": 10,
            "TIMEKPR_SESSION_TYPES_CTRL": ["x11", "wayland"],
            "TIMEKPR_SESSION_TYPES_EXCL": ["tty"],
            "TIMEKPR_USERS_EXCL": ["root"],
        }

    def isConnected(self):
        return True, True

    def initTimekprConnection(self, pTryOnce, pRescheduleConnection=False):
        pass

    def getUserList(self):
        self.user_list_calls += 1
        return (
            0,
            "",
            [
                ["alice", "Alice", self.users["alice"]["POLICY_SOURCE"]],
                ["bob@idm.nixos.test", "", "default"],
            ],
        )

    def getUserConfigurationAndInformation(self, name, level):
        if is_group(name):
            if name not in self.groups:
                return (
                    -1,
                    msg.getTranslation("TK_MSG_CONFIG_LOADER_GROUPCONFIG_NOTFOUND")
                    % (name[1:]),
                    {},
                )
            return 0, "", dict(self.groups[name])
        if name not in self.users:
            return (
                -1,
                msg.getTranslation("TK_MSG_CONFIG_LOADER_USER_NOTFOUND") % (name),
                {},
            )
        info = dict(self.users[name])
        if name == "alice":
            info.update(LIVE)
        return 0, "", info

    def getGroupList(self):
        return (
            0,
            "",
            [
                [
                    name[1:],
                    ";".join(str(group) for group in policy["OVERRIDES"]),
                    GROUPS.get(name[1:], ("", ""))[1],
                ]
                for name, policy in sorted(self.groups.items())
            ],
        )

    def getTimekprConfiguration(self):
        return 0, "", dict(self.server)

    def _policy(self, target):
        """The policy a setter writes to, created on demand as the daemon does"""
        if is_group(target):
            self.policies.add(target)
            return self.groups.setdefault(target, default_group())
        if target not in self.policies:
            self.policies.add(target)
            # a setter creates a policy for any name, known or not
            self.users.setdefault(target, default_user())["POLICY_SOURCE"] = "user"
        return self.users[target]

    def setOverrides(self, target, overrides):
        self.calls.append(("setOverrides", (target, overrides)))
        if not is_group(target):
            return -1, msg.getTranslation("TK_MSG_USER_ADMIN_CHK_USER_NOT_GROUP") % (
                target
            )
        policy = self._policy(target)
        policy["OVERRIDES"] = [dbus.String(str(group)) for group in overrides]
        if "overrides" not in [str(name) for name in policy["POLICY_SETTINGS"]]:
            policy["POLICY_SETTINGS"] = [
                *policy["POLICY_SETTINGS"],
                dbus.String("overrides"),
            ]
        return 0, ""

    def unsetSetting(self, target, setting):
        """The daemon's answers: an unknown setting, a setting the target
        cannot have, a policy that does not set it; the keys go back to
        the defaults, and a user policy left with nothing is deleted"""
        self.calls.append(("unsetSetting", (target, setting)))
        if setting not in SETTING_KEYS:
            return -1, msg.getTranslation("TK_MSG_USER_ADMIN_CHK_SETTING_INVALID") % (
                setting
            )
        if setting == "hide_tray_icon" and is_group(target):
            return -1, msg.getTranslation("TK_MSG_USER_ADMIN_CHK_GROUP_NOT_USER") % (
                target[1:]
            )
        if setting == "overrides" and not is_group(target):
            return -1, msg.getTranslation("TK_MSG_USER_ADMIN_CHK_USER_NOT_GROUP") % (
                target
            )
        policy = self.groups.get(target) if is_group(target) else self.users.get(target)
        held = (
            [] if policy is None else [str(name) for name in policy["POLICY_SETTINGS"]]
        )
        names = (
            [f"allowed_hours_{day}" for day in range(1, 8)]
            if setting == "allowed_hours"
            else ["allowed_days", "limits_per_day"]
            if setting in ("allowed_days", "limits_per_day")
            else [setting]
        )
        if target not in self.policies or not any(name in held for name in names):
            return -1, msg.getTranslation("TK_MSG_CONFIG_LOADER_SETTING_NOTSET") % (
                target,
                setting,
            )
        defaults = default_group() if is_group(target) else default_user()
        for key in SETTING_KEYS[setting]:
            policy[key] = defaults[key]
        policy["POLICY_SETTINGS"] = [
            dbus.String(name) for name in held if name not in names
        ]
        if not is_group(target) and not policy["POLICY_SETTINGS"]:
            self.deletePolicy(target)
        return 0, ""

    def deletePolicy(self, target):
        self.calls.append(("deletePolicy", (target,)))
        if target not in self.policies:
            return -1, msg.getTranslation("TK_MSG_CONFIG_LOADER_POLICY_NOTFOUND") % (
                target
            )
        self.policies.remove(target)
        if is_group(target):
            del self.groups[target]
        else:
            self.users[target]["POLICY_SOURCE"] = "default"
        return 0, ""

    def applyPolicyChanges(self, target, unset, changes):
        """The daemon's all-or-nothing change of a policy: the settings in
        unset taken out, then the ones in changes set in the daemon's order
        (the limits aligned with the allowed days), everything undone when
        one is refused; the setters it runs are not logged as calls"""
        self.calls.append(("applyPolicyChanges", (target, list(unset), dict(changes))))
        saved = copy.deepcopy((self.users, self.groups, self.policies))
        logged = len(self.calls)
        result = self._apply_policy_changes(target, unset, changes)
        del self.calls[logged:]
        if result[0] != 0:
            self.users, self.groups, self.policies = saved
        return result

    def _apply_policy_changes(self, target, unset, changes):
        if is_group(target):
            # a group's policy, even an empty one
            self._policy(target)
        for setting in unset:
            result, message = self.unsetSetting(target, setting)
            if result != 0:
                return result, message, setting
        for setting in changes:
            if setting not in SETTING_KEYS:
                return (
                    -1,
                    msg.getTranslation("TK_MSG_USER_ADMIN_CHK_SETTING_INVALID")
                    % (setting),
                    setting,
                )
        steps = []
        if "allowed_days" in changes or "limits_per_day" in changes:
            current = (self.groups if is_group(target) else self.users).get(
                target, default_user()
            )
            by_day = {
                str(day): int(limit)
                for day, limit in zip(
                    current["ALLOWED_WEEKDAYS"], current["LIMITS_PER_WEEKDAYS"]
                )
            }
            days = (
                [
                    str(day)
                    for day in sorted({int(day) for day in changes["allowed_days"]})
                ]
                if "allowed_days" in changes
                else [str(day) for day in current["ALLOWED_WEEKDAYS"]]
            )
            by_day.update(changes.get("limits_per_day", {}))
            if "allowed_days" in changes:
                steps.append(("allowed_days", "setAllowedDays", days))
            steps.append(
                (
                    "limits_per_day",
                    "setTimeLimitForDays",
                    [by_day.get(day, 0) for day in days],
                )
            )
        for setting, setter in (
            ("allowed_hours", "setAllowedHours"),
            *[(f"allowed_hours_{day}", "setAllowedHours") for day in range(1, 8)],
            ("limit_per_week", "setTimeLimitForWeek"),
            ("limit_per_month", "setTimeLimitForMonth"),
            ("track_inactive", "setTrackInactive"),
            ("hide_tray_icon", "setHideTrayIcon"),
            ("overrides", "setOverrides"),
        ):
            if setting not in changes:
                continue
            if setter == "setAllowedHours":
                day = "ALL" if setting == "allowed_hours" else setting.rsplit("_", 1)[1]
                steps.append((setting, setter, day, changes[setting]))
            else:
                steps.append((setting, setter, changes[setting]))
        for setting, setter, *args in steps:
            result, message = getattr(self, setter)(target, *args)
            if result != 0:
                return result, message, setting
        return 0, "", ""

    def migratePolicies(self, dry_run):
        self.calls.append(("migratePolicies", (dry_run,)))
        return 0, "", [dbus.String("carol")]

    def __getattr__(self, name):
        if not name.startswith("set"):
            raise AttributeError(name)

        def setter(*args):
            self.calls.append((name, args))
            held = None
            if name == "setAllowedDays":
                held = ("allowed_days", "limits_per_day")
                self._policy(args[0])["ALLOWED_WEEKDAYS"] = list(args[1])
            elif name == "setTimeLimitForDays":
                self._policy(args[0])["LIMITS_PER_WEEKDAYS"] = list(args[1])
                held = ("allowed_days", "limits_per_day")
            elif name == "setAllowedHours":
                days = range(1, 8) if args[1] == "ALL" else [int(args[1])]
                for day in days:
                    self._policy(args[0])[f"ALLOWED_HOURS_{day}"] = args[2]
                held = tuple(f"allowed_hours_{day}" for day in days)
            elif name == "setTimeLimitForWeek" and args[1] == 13:
                return -1, "unlucky"
            elif name in ("setHideTrayIcon", "setTimeLeft") and is_group(args[0]):
                return -1, msg.getTranslation(
                    "TK_MSG_USER_ADMIN_CHK_GROUP_NOT_USER"
                ) % (args[0][1:])
            elif name in ("setTimeLimitForWeek", "setTimeLimitForMonth"):
                week = name == "setTimeLimitForWeek"
                self._policy(args[0])[
                    "LIMIT_PER_WEEK" if week else "LIMIT_PER_MONTH"
                ] = args[1]
                held = ("limit_per_week" if week else "limit_per_month",)
            elif name == "setTrackInactive":
                self._policy(args[0])["TRACK_INACTIVE"] = dbus.Boolean(args[1])
                held = ("track_inactive",)
            elif name == "setHideTrayIcon":
                self._policy(args[0])["HIDE_TRAY_ICON"] = dbus.Boolean(args[1])
                held = ("hide_tray_icon",)
            elif name == "setTimekprPollTime":
                self.server["TIMEKPR_POLLTIME"] = args[0]
            elif name == "setTimekprLogLevel":
                return -1, msg.getTranslation(
                    "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
                )
            if held is not None:
                policy = self._policy(args[0])
                policy["POLICY_SETTINGS"] = [
                    dbus.String(name)
                    for name in SETTING_KEYS
                    if name != "allowed_hours"
                    and (
                        name in held
                        or name in [str(name) for name in policy["POLICY_SETTINGS"]]
                    )
                ]
            return 0, ""

        return setter
