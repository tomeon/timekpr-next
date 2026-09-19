"""A stand-in for timekprAdminConnector: the daemon's data shapes, the
daemon's (result, message[, payload]) convention, and just enough state
to read back what the setters wrote."""

import dbus

from timekpr.common.constants import messages as msg

ALL_HOURS = {str(hour): {"STARTMIN": 0, "ENDMIN": 60, "UACC": 0} for hour in range(24)}
LIVE = {
    "ACTUAL_TIME_SPENT_SESSION": 10,
    "ACTUAL_TIME_INACTIVE_SESSION": 11,
    "ACTUAL_TIME_SPENT_BALANCE": 12,
    "ACTUAL_TIME_SPENT_DAY": 13,
    "ACTUAL_TIME_LEFT_DAY": 14,
    "ACTUAL_TIME_LEFT_CONTINUOUS": 15,
    "ACTUAL_PLAYTIME_LEFT_DAY": 16,
    "ACTUAL_ACTIVE_PLAYTIME_ACTIVITY_COUNT": 0,
}


def default_user():
    """A user's full information as the daemon returns it, with dbus types
    where the daemon uses them."""
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
            "LOCKOUT_TYPE": "terminate",
            "LIMIT_PER_WEEK": 604800,
            "LIMIT_PER_MONTH": 2678400,
            "PLAYTIME_ENABLED": False,
            "PLAYTIME_LIMIT_OVERRIDE_ENABLED": False,
            "PLAYTIME_UNACCOUNTED_INTERVALS_ENABLED": False,
            "PLAYTIME_ALLOWED_WEEKDAYS": ["1", "2"],
            "PLAYTIME_LIMITS_PER_WEEKDAYS": [1800, 1800],
            "PLAYTIME_ACTIVITIES": [["csgo_linux", "CS: GO"]],
            "TIME_SPENT_BALANCE": 1,
            "TIME_SPENT_DAY": 2,
            "TIME_SPENT_WEEK": 3,
            "TIME_SPENT_MONTH": 4,
            "TIME_LEFT_DAY": 5,
            "PLAYTIME_LEFT_DAY": 6,
            "PLAYTIME_SPENT_DAY": 7,
        }
    )
    return info


class FakeConnector:
    """alice has an active session, bob@idm.nixos.test has none.  A week
    limit of 13 is refused with the daemon's -1 convention, and setting the
    log level fails the way the daemon fails on a read-only /etc/timekpr."""

    def __init__(self):
        # the setters called, in order; reads are counted separately
        self.calls = []
        self.user_list_calls = 0
        self.users = {"alice": default_user(), "bob@idm.nixos.test": default_user()}
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
            "TIMEKPR_PLAYTIME_ENABLED": True,
            "TIMEKPR_PLAYTIME_ENHANCED_ACTIVITY_MONITOR_ENABLED": False,
        }

    def isConnected(self):
        return True, True

    def initTimekprConnection(self, pTryOnce, pRescheduleConnection=False):
        pass

    def getUserList(self):
        self.user_list_calls += 1
        return 0, "", [["alice", "Alice"], ["bob@idm.nixos.test", ""]]

    def getUserConfigurationAndInformation(self, name, level):
        info = dict(self.users[name])
        if name == "alice":
            info.update(LIVE)
        return 0, "", info

    def getTimekprConfiguration(self):
        return 0, "", dict(self.server)

    def __getattr__(self, name):
        if not name.startswith("set"):
            raise AttributeError(name)

        def setter(*args):
            self.calls.append((name, args))
            if name == "setAllowedDays":
                self.users[args[0]]["ALLOWED_WEEKDAYS"] = list(args[1])
            elif name == "setTimeLimitForDays":
                self.users[args[0]]["LIMITS_PER_WEEKDAYS"] = list(args[1])
            elif name == "setAllowedHours":
                for day in range(1, 8) if args[1] == "ALL" else [int(args[1])]:
                    self.users[args[0]][f"ALLOWED_HOURS_{day}"] = args[2]
            elif name == "setLockoutType":
                self.users[args[0]]["LOCKOUT_TYPE"] = args[1]
                self.users[args[0]]["WAKEUP_HOUR_INTERVAL"] = f"{args[2]};{args[3]}"
            elif name == "setTimeLimitForWeek" and args[1] == 13:
                return -1, "unlucky"
            elif name == "setTimekprPollTime":
                self.server["TIMEKPR_POLLTIME"] = args[0]
            elif name == "setTimekprLogLevel":
                return -1, msg.getTranslation(
                    "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
                )
            return 0, ""

        return setter
