"""
timekprw: bridge between the web API models and the timekpr daemon.

Every call goes through timekprAdminConnector, the same D-Bus client that
timekpra and the GTK administration tool use, so the daemon's own
validation applies unchanged.  This module only translates between the
JSON shapes of docs/web-api.md and the daemon's argument and result
conventions.
"""
import threading

import dbus

from timekpr.common.constants import constants as cons
from timekpr.common.constants import messages as msg
from timekpr.web import models

# the daemon reports "not connected" and "call failed" through these codes
_RESULT_NOT_READY = -2

# API field -> daemon key / setter for scalar daemon-wide settings
SERVER_FIELDS = {
    "log_level": ("TIMEKPR_LOGLEVEL", "setTimekprLogLevel"),
    "poll_time": ("TIMEKPR_POLLTIME", "setTimekprPollTime"),
    "save_time": ("TIMEKPR_SAVE_TIME", "setTimekprSaveTime"),
    "termination_time": ("TIMEKPR_TERMINATION_TIME", "setTimekprTerminationTime"),
    "final_warning_time": ("TIMEKPR_FINAL_WARNING_TIME", "setTimekprFinalWarningTime"),
    "final_notification_time": ("TIMEKPR_FINAL_NOTIFICATION_TIME", "setTimekprFinalNotificationTime"),
    "session_types_tracked": ("TIMEKPR_SESSION_TYPES_CTRL", "setTimekprSessionsCtrl"),
    "session_types_excluded": ("TIMEKPR_SESSION_TYPES_EXCL", "setTimekprSessionsExcl"),
    "users_excluded": ("TIMEKPR_USERS_EXCL", "setTimekprUsersExcl"),
    "playtime_enabled": ("TIMEKPR_PLAYTIME_ENABLED", "setTimekprPlayTimeEnabled"),
    "playtime_enhanced_activity_monitor": ("TIMEKPR_PLAYTIME_ENHANCED_ACTIVITY_MONITOR_ENABLED", "setTimekprPlayTimeEnhancedActivityMonitorEnabled"),
}

# API field -> setter for scalar per-user settings
USER_SETTERS = (
    ("limit_per_week", "setTimeLimitForWeek"),
    ("limit_per_month", "setTimeLimitForMonth"),
    ("track_inactive", "setTrackInactive"),
    ("hide_tray_icon", "setHideTrayIcon"),
)
PLAYTIME_SETTERS = (
    ("enabled", "setPlayTimeEnabled"),
    ("limit_override", "setPlayTimeLimitOverride"),
    ("allow_unaccounted_intervals", "setPlayTimeUnaccountedIntervalsEnabled"),
)

# saved counters (always present) and live counters (present while the
# daemon tracks a session of the user), API field -> daemon key
STATUS_SAVED = {
    "time_spent_balance": "TIME_SPENT_BALANCE",
    "time_spent_day": "TIME_SPENT_DAY",
    "time_spent_week": "TIME_SPENT_WEEK",
    "time_spent_month": "TIME_SPENT_MONTH",
    "time_left_day": "TIME_LEFT_DAY",
    "playtime_spent_day": "PLAYTIME_SPENT_DAY",
    "playtime_left_day": "PLAYTIME_LEFT_DAY",
}
STATUS_LIVE = {
    "time_spent_balance": "ACTUAL_TIME_SPENT_BALANCE",
    "time_spent_day": "ACTUAL_TIME_SPENT_DAY",
    "time_left_day": "ACTUAL_TIME_LEFT_DAY",
    "playtime_left_day": "ACTUAL_PLAYTIME_LEFT_DAY",
    "time_left_continuous": "ACTUAL_TIME_LEFT_CONTINUOUS",
    "time_spent_session": "ACTUAL_TIME_SPENT_SESSION",
    "time_inactive_session": "ACTUAL_TIME_INACTIVE_SESSION",
    "playtime_active_activity_count": "ACTUAL_ACTIVE_PLAYTIME_ACTIVITY_COUNT",
}

TIME_LEFT_OPERATIONS = {"add": "+", "subtract": "-", "set": "="}
# the CLI's defaults for the wake-up interval of lockout types other than suspendwake
DEFAULT_WAKE = (0, 23)
WEEKDAYS = [int(day) for day in cons.TK_ALLOWED_WEEKDAYS.split(";")]


class DaemonError(Exception):
    """A request the daemon (or the connection to it) refused"""

    def __init__(self, status, detail, field=None, applied=()):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.field = field
        self.applied = list(applied)


def plain(value):
    """Convert dbus-python values to plain Python values, recursively"""
    if isinstance(value, dict):
        return {str(key): plain(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(val) for val in value]
    if isinstance(value, dbus.Boolean):
        return bool(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return value


# ## conversions between daemon values and API models ##

def limits_by_day(days, limits):
    """The daemon stores per-day limits positionally against the allowed days"""
    return {day: (limits[idx] if idx < len(limits) else 0) for idx, day in enumerate(days)}


def limits_list(days, by_day):
    return [by_day.get(day, 0) for day in days]


def hours_from_daemon(hours):
    return [
        models.HourEntry(hour=int(hour), start_minute=spec[cons.TK_CTRL_SMIN], end_minute=spec[cons.TK_CTRL_EMIN], unaccounted=bool(spec[cons.TK_CTRL_UACC]))
        for hour, spec in sorted(hours.items(), key=lambda item: int(item[0]))
    ]


def hours_to_daemon(entries):
    return {str(entry.hour): {cons.TK_CTRL_SMIN: entry.start_minute, cons.TK_CTRL_EMIN: entry.end_minute, cons.TK_CTRL_UACC: int(entry.unaccounted)} for entry in entries}


def lockout_from_daemon(info):
    lockout = {"type": info["LOCKOUT_TYPE"]}
    if lockout["type"] == cons.TK_CTRL_RES_W:
        wake = [int(hour) for hour in info.get("WAKEUP_HOUR_INTERVAL", "").split(";") if hour != ""]
        lockout["wake_from"], lockout["wake_to"] = wake if len(wake) == 2 else DEFAULT_WAKE
    return models.Lockout(**lockout)


def config_from_daemon(info):
    days = [int(day) for day in info["ALLOWED_WEEKDAYS"]]
    playtime_days = [int(day) for day in info["PLAYTIME_ALLOWED_WEEKDAYS"]]
    return models.UserConfig(
        allowed_days=days,
        limits_per_day=limits_by_day(days, info["LIMITS_PER_WEEKDAYS"]),
        allowed_hours={day: hours_from_daemon(info["ALLOWED_HOURS_%s" % day]) for day in WEEKDAYS},
        limit_per_week=info["LIMIT_PER_WEEK"],
        limit_per_month=info["LIMIT_PER_MONTH"],
        track_inactive=info["TRACK_INACTIVE"],
        hide_tray_icon=info["HIDE_TRAY_ICON"],
        lockout=lockout_from_daemon(info),
        playtime=models.PlayTimeConfig(
            enabled=info["PLAYTIME_ENABLED"],
            limit_override=info["PLAYTIME_LIMIT_OVERRIDE_ENABLED"],
            allow_unaccounted_intervals=info["PLAYTIME_UNACCOUNTED_INTERVALS_ENABLED"],
            allowed_days=playtime_days,
            limits_per_day=limits_by_day(playtime_days, info["PLAYTIME_LIMITS_PER_WEEKDAYS"]),
            activities=[models.Activity(process=activity[0], description=activity[1]) for activity in info["PLAYTIME_ACTIVITIES"]],
        ),
    )


def status_from_daemon(info):
    values = {field: info[key] for field, key in STATUS_SAVED.items()}
    active = all(key in info for key in STATUS_LIVE.values())
    if active:
        values.update({field: info[key] for field, key in STATUS_LIVE.items()})
    return models.UserStatus(session_active=active, **values)


def server_config_from_daemon(info):
    return models.ServerConfig(**{field: info[key] for field, (key, _setter) in SERVER_FIELDS.items()})


# ## the bridge ##

class Bridge(object):
    """Serialized access to the daemon through timekprAdminConnector"""

    def __init__(self, connector=None):
        self._connector = connector
        self._lock = threading.Lock()

    def _connect(self):
        """Return a connected connector or raise DaemonError"""
        if self._connector is None:
            # importing dbus-related modules is deferred so that the API
            # models can be used without a system bus (tests)
            from timekpr.client.interface.dbus.administration import timekprAdminConnector
            try:
                self._connector = timekprAdminConnector()
            except dbus.DBusException as ex:
                raise DaemonError(503, "cannot connect to the system bus: %s" % (ex))
        if not self._connector.isConnected()[0]:
            self._connector.initTimekprConnection(True, True)
        if not self._connector.isConnected()[0]:
            raise DaemonError(503, "the timekpr daemon is not reachable")
        return self._connector

    def _call(self, method, *args):
        """Call a connector method; return its payload (if any) or raise DaemonError"""
        with self._lock:
            connector = self._connect()
            result = plain(getattr(connector, method)(*args))
            connected = connector.isConnected()[0]
        code, message = result[0], result[1]
        if code == _RESULT_NOT_READY or (code != 0 and not connected):
            raise DaemonError(503, message)
        if code != 0 and message == msg.getTranslation("TK_MSG_DBUS_COMMUNICATION_COMMAND_FAILED"):
            # the daemon's D-Bus policy refused us: timekprw is not root or in the timekpr group
            raise DaemonError(502, message)
        if code != 0:
            raise DaemonError(400, message)
        return result[2] if len(result) > 2 else None

    # ## service ##

    def health(self):
        try:
            self._call("getUserList")
            return models.Health(daemon="ok", timekpr_version=cons.TK_VERSION)
        except DaemonError:
            return models.Health(daemon="unreachable", timekpr_version=cons.TK_VERSION)

    def get_server_config(self):
        return server_config_from_daemon(self._call("getTimekprConfiguration"))

    def patch_server_config(self, patch):
        steps = Steps(self)
        for field, (_key, setter) in SERVER_FIELDS.items():
            value = getattr(patch, field)
            if value is not None:
                steps.run(field, setter, value)
        return self.get_server_config()

    # ## users ##

    def list_users(self, include_status=False):
        users = [models.UserSummary(username=user[0], full_name=user[1]) for user in self._call("getUserList")]
        if include_status:
            for user in users:
                user.status = self.get_user_status(user.username)
        return users

    def _require_user(self, username):
        if username not in [user[0] for user in self._call("getUserList")]:
            raise DaemonError(404, "timekpr has no configuration for user %s" % (username))

    def _user_info(self, username, level):
        self._require_user(username)
        return self._call("getUserConfigurationAndInformation", username, level)

    def get_user(self, username):
        info = self._user_info(username, cons.TK_CL_INF_FULL)
        return models.User(username=username, config=config_from_daemon(info), status=status_from_daemon(info))

    def get_user_config(self, username):
        return config_from_daemon(self._user_info(username, cons.TK_CL_INF_FULL))

    def get_user_status(self, username):
        return status_from_daemon(self._user_info(username, cons.TK_CL_INF_FULL))

    def patch_user_config(self, username, patch):
        current = self.get_user_config(username)
        steps = Steps(self, username)
        apply_days_and_limits(steps, "", patch, current, "setAllowedDays", "setTimeLimitForDays")
        if patch.allowed_hours is not None:
            for day, entries in patch.allowed_hours.items():
                steps.run("allowed_hours.%s" % (day), "setAllowedHours", str(day), hours_to_daemon(entries))
        apply_scalars(steps, "", patch, USER_SETTERS)
        if patch.lockout is not None:
            wake_from, wake_to = (patch.lockout.wake_from, patch.lockout.wake_to) if patch.lockout.type == cons.TK_CTRL_RES_W else DEFAULT_WAKE
            steps.run("lockout", "setLockoutType", patch.lockout.type, str(wake_from), str(wake_to))
        if patch.playtime is not None:
            apply_days_and_limits(steps, "playtime.", patch.playtime, current.playtime, "setPlayTimeAllowedDays", "setPlayTimeLimitsForDays")
            apply_scalars(steps, "playtime.", patch.playtime, PLAYTIME_SETTERS)
            if patch.playtime.activities is not None:
                steps.run("playtime.activities", "setPlayTimeActivities", [[activity.process, activity.description] for activity in patch.playtime.activities])
        return self.get_user_config(username)

    def set_allowed_hours(self, username, day, entries):
        """day is an ISO weekday or "all" """
        self._require_user(username)
        Steps(self, username).run("allowed_hours.%s" % (day), "setAllowedHours", "ALL" if day == "all" else str(day), hours_to_daemon(entries))
        return self.get_user_config(username)

    def set_time_left(self, username, request, playtime=False):
        self._require_user(username)
        self._call("setPlayTimeLeft" if playtime else "setTimeLeft", username, TIME_LEFT_OPERATIONS[request.operation], request.seconds)
        return self.get_user_status(username)


class Steps(object):
    """Runs the setters of a PATCH one by one, remembering which fields
    were written so that a failure can report them"""

    def __init__(self, bridge, username=None):
        self._bridge = bridge
        self._prefix = () if username is None else (username,)
        self.applied = []

    def run(self, field, setter, *args):
        try:
            self._bridge._call(setter, *self._prefix, *args)
        except DaemonError as ex:
            raise DaemonError(ex.status, ex.detail, field, self.applied)
        self.applied.append(field)


def apply_scalars(steps, prefix, patch, setters):
    for field, setter in setters:
        value = getattr(patch, field)
        if value is not None:
            steps.run(prefix + field, setter, value)


def apply_days_and_limits(steps, prefix, patch, current, days_setter, limits_setter):
    """Allowed days and their limits are coupled: the daemon stores limits
    positionally against the allowed days, so whenever either changes the
    limits are re-sent aligned with the (new) allowed days"""
    days = sorted(set(patch.allowed_days)) if patch.allowed_days is not None else current.allowed_days
    if patch.allowed_days is not None:
        steps.run(prefix + "allowed_days", days_setter, [str(day) for day in days])
    if patch.allowed_days is not None or patch.limits_per_day is not None:
        limits = {**current.limits_per_day, **(patch.limits_per_day or {})}
        steps.run(prefix + "limits_per_day", limits_setter, limits_list(days, limits))
