"""
Conversions between the timekpr daemon's data shapes and the web API's
JSON shapes (docs/web-api.md), as plain Python values.

Used by timekprw (daemon -> JSON, when serving) and by timekpra's HTTP
connector (JSON -> daemon, when talking to timekprw), so that both
directions share one set of field names and rules.  Nothing here
depends on D-Bus or on the web framework.
"""

from timekpr.common.constants import constants as cons

WEEKDAYS = [int(day) for day in cons.TK_ALLOWED_WEEKDAYS.split(";")]

# API field -> daemon key / setter for scalar daemon-wide settings
SERVER_FIELDS = {
    "log_level": ("TIMEKPR_LOGLEVEL", "setTimekprLogLevel"),
    "poll_time": ("TIMEKPR_POLLTIME", "setTimekprPollTime"),
    "save_time": ("TIMEKPR_SAVE_TIME", "setTimekprSaveTime"),
    "termination_time": ("TIMEKPR_TERMINATION_TIME", "setTimekprTerminationTime"),
    "final_warning_time": ("TIMEKPR_FINAL_WARNING_TIME", "setTimekprFinalWarningTime"),
    "final_notification_time": (
        "TIMEKPR_FINAL_NOTIFICATION_TIME",
        "setTimekprFinalNotificationTime",
    ),
    "session_types_tracked": ("TIMEKPR_SESSION_TYPES_CTRL", "setTimekprSessionsCtrl"),
    "session_types_excluded": ("TIMEKPR_SESSION_TYPES_EXCL", "setTimekprSessionsExcl"),
    "users_excluded": ("TIMEKPR_USERS_EXCL", "setTimekprUsersExcl"),
    "playtime_enabled": ("TIMEKPR_PLAYTIME_ENABLED", "setTimekprPlayTimeEnabled"),
    "playtime_enhanced_activity_monitor": (
        "TIMEKPR_PLAYTIME_ENHANCED_ACTIVITY_MONITOR_ENABLED",
        "setTimekprPlayTimeEnhancedActivityMonitorEnabled",
    ),
}

# API field -> daemon key / setter for scalar per-user settings
USER_FIELDS = {
    "limit_per_week": ("LIMIT_PER_WEEK", "setTimeLimitForWeek"),
    "limit_per_month": ("LIMIT_PER_MONTH", "setTimeLimitForMonth"),
    "track_inactive": ("TRACK_INACTIVE", "setTrackInactive"),
    "hide_tray_icon": ("HIDE_TRAY_ICON", "setHideTrayIcon"),
}
PLAYTIME_FIELDS = {
    "enabled": ("PLAYTIME_ENABLED", "setPlayTimeEnabled"),
    "limit_override": ("PLAYTIME_LIMIT_OVERRIDE_ENABLED", "setPlayTimeLimitOverride"),
    "allow_unaccounted_intervals": (
        "PLAYTIME_UNACCOUNTED_INTERVALS_ENABLED",
        "setPlayTimeUnaccountedIntervalsEnabled",
    ),
}

# saved counters (always present) and live counters (present while the
# daemon tracks a session of the user), API field -> daemon key
STATUS_SAVED = {
    "time_spent_balance": "TIME_SPENT_BALANCE",
    "time_spent_day": "TIME_SPENT_DAY",
    "time_spent_week": "TIME_SPENT_WEEK",
    "time_spent_month": "TIME_SPENT_MONTH",
    "time_left_day": "TIME_LEFT_DAY",
    "playtime_left_day": "PLAYTIME_LEFT_DAY",
    "playtime_spent_day": "PLAYTIME_SPENT_DAY",
}
STATUS_LIVE = {
    "time_spent_session": "ACTUAL_TIME_SPENT_SESSION",
    "time_inactive_session": "ACTUAL_TIME_INACTIVE_SESSION",
    "time_spent_balance": "ACTUAL_TIME_SPENT_BALANCE",
    "time_spent_day": "ACTUAL_TIME_SPENT_DAY",
    "time_left_day": "ACTUAL_TIME_LEFT_DAY",
    "time_left_continuous": "ACTUAL_TIME_LEFT_CONTINUOUS",
    "playtime_left_day": "ACTUAL_PLAYTIME_LEFT_DAY",
    "playtime_active_activity_count": "ACTUAL_ACTIVE_PLAYTIME_ACTIVITY_COUNT",
}

# API operation -> daemon operation for adjusting time left
TIME_LEFT_OPERATIONS = {"add": "+", "subtract": "-", "set": "="}
# the CLI's defaults for the wake-up interval of lockout types other than suspendwake
DEFAULT_WAKE = (0, 23)


def limits_by_day(days, limits):
    """The daemon stores per-day limits positionally against the allowed days"""
    return {
        day: (limits[idx] if idx < len(limits) else 0) for idx, day in enumerate(days)
    }


def limits_list(days, by_day):
    return [int(by_day.get(day, by_day.get(str(day), 0))) for day in days]


def hours_from_daemon(hours):
    """{"7": {STARTMIN, ENDMIN, UACC}} -> [{"hour": 7, ...}], sorted by hour"""
    return [
        {
            "hour": int(hour),
            "start_minute": int(spec[cons.TK_CTRL_SMIN]),
            "end_minute": int(spec[cons.TK_CTRL_EMIN]),
            "unaccounted": bool(spec[cons.TK_CTRL_UACC]),
        }
        for hour, spec in sorted(hours.items(), key=lambda item: int(item[0]))
    ]


def hours_to_daemon(entries):
    return {
        str(entry["hour"]): {
            cons.TK_CTRL_SMIN: entry.get("start_minute", 0),
            cons.TK_CTRL_EMIN: entry.get("end_minute", 60),
            cons.TK_CTRL_UACC: int(entry.get("unaccounted", False)),
        }
        for entry in entries
    }


def lockout_from_daemon(info):
    lockout = {"type": info["LOCKOUT_TYPE"], "wake_from": None, "wake_to": None}
    if lockout["type"] == cons.TK_CTRL_RES_W:
        wake = [
            int(hour)
            for hour in info.get("WAKEUP_HOUR_INTERVAL", "").split(";")
            if hour != ""
        ]
        lockout["wake_from"], lockout["wake_to"] = (
            wake if len(wake) == 2 else DEFAULT_WAKE
        )
    return lockout


def lockout_wake(lockout):
    """The wake-up interval to send to the daemon for a lockout setting"""
    if lockout["type"] == cons.TK_CTRL_RES_W:
        return lockout["wake_from"], lockout["wake_to"]
    return DEFAULT_WAKE


# ## whole resources ##


def user_config_from_daemon(info):
    days = [int(day) for day in info["ALLOWED_WEEKDAYS"]]
    playtime_days = [int(day) for day in info["PLAYTIME_ALLOWED_WEEKDAYS"]]
    config = {
        "allowed_days": days,
        "limits_per_day": limits_by_day(days, info["LIMITS_PER_WEEKDAYS"]),
        "allowed_hours": {
            day: hours_from_daemon(info["ALLOWED_HOURS_%s" % day]) for day in WEEKDAYS
        },
        "lockout": lockout_from_daemon(info),
        "playtime": {
            "allowed_days": playtime_days,
            "limits_per_day": limits_by_day(
                playtime_days, info["PLAYTIME_LIMITS_PER_WEEKDAYS"]
            ),
            "activities": [
                {"process": activity[0], "description": activity[1]}
                for activity in info["PLAYTIME_ACTIVITIES"]
            ],
        },
    }
    config.update({field: info[key] for field, (key, _setter) in USER_FIELDS.items()})
    config["playtime"].update(
        {field: info[key] for field, (key, _setter) in PLAYTIME_FIELDS.items()}
    )
    return config


def user_config_to_daemon(config):
    """The inverse of user_config_from_daemon, in the daemon's key order"""
    days = config["allowed_days"]
    playtime = config["playtime"]
    hours = config["allowed_hours"]
    info = {
        "ALLOWED_HOURS_%s" % day: hours_to_daemon(
            hours.get(str(day), hours.get(day, []))
        )
        for day in WEEKDAYS
    }
    info["ALLOWED_WEEKDAYS"] = [str(day) for day in days]
    info["LIMITS_PER_WEEKDAYS"] = limits_list(days, config["limits_per_day"])
    info["TRACK_INACTIVE"] = config["track_inactive"]
    info["HIDE_TRAY_ICON"] = config["hide_tray_icon"]
    info["LOCKOUT_TYPE"] = config["lockout"]["type"]
    if info["LOCKOUT_TYPE"] == cons.TK_CTRL_RES_W:
        info["WAKEUP_HOUR_INTERVAL"] = "%s;%s" % lockout_wake(config["lockout"])
    info["LIMIT_PER_WEEK"] = config["limit_per_week"]
    info["LIMIT_PER_MONTH"] = config["limit_per_month"]
    for field, (key, _setter) in PLAYTIME_FIELDS.items():
        info[key] = playtime[field]
    info["PLAYTIME_ALLOWED_WEEKDAYS"] = [str(day) for day in playtime["allowed_days"]]
    info["PLAYTIME_LIMITS_PER_WEEKDAYS"] = limits_list(
        playtime["allowed_days"], playtime["limits_per_day"]
    )
    info["PLAYTIME_ACTIVITIES"] = [
        [activity["process"], activity["description"]]
        for activity in playtime["activities"]
    ]
    return info


def user_status_from_daemon(info):
    status = {field: info[key] for field, key in STATUS_SAVED.items()}
    status["session_active"] = all(key in info for key in STATUS_LIVE.values())
    # live counters supersede the saved ones while a session is active;
    # the live-only ones are null otherwise
    if status["session_active"]:
        status.update({field: info[key] for field, key in STATUS_LIVE.items()})
    else:
        status.update(
            {field: None for field in STATUS_LIVE if field not in STATUS_SAVED}
        )
    return status


def user_status_to_daemon(status, saved=True, live=True):
    """The inverse of user_status_from_daemon: the saved counters and,
    while a session is active, the live ones (as the daemon returns them
    for the info levels "S" and "R" respectively)"""
    info = {}
    if saved:
        info.update({key: status[field] for field, key in STATUS_SAVED.items()})
    if live and status["session_active"]:
        info.update({key: status[field] for field, key in STATUS_LIVE.items()})
    return info


def server_config_from_daemon(info):
    return {field: info[key] for field, (key, _setter) in SERVER_FIELDS.items()}


def server_config_to_daemon(config):
    return {key: config[field] for field, (key, _setter) in SERVER_FIELDS.items()}
