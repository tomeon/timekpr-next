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
}

# API field -> daemon key / setter for scalar per-user settings
USER_FIELDS = {
    "limit_per_week": ("LIMIT_PER_WEEK", "setTimeLimitForWeek"),
    "limit_per_month": ("LIMIT_PER_MONTH", "setTimeLimitForMonth"),
    "track_inactive": ("TRACK_INACTIVE", "setTrackInactive"),
    "hide_tray_icon": ("HIDE_TRAY_ICON", "setHideTrayIcon"),
}

# the scalar per-group settings: a group policy has no tray icon (that is
# a per-user preference)
GROUP_FIELDS = {
    field: setter for field, setter in USER_FIELDS.items() if field != "hide_tray_icon"
}

# where a user's effective policy comes from, API field -> daemon key
POLICY_FIELDS = {"policy_source": "POLICY_SOURCE", "policy_groups": "POLICY_GROUPS"}

# saved counters (always present) and live counters (present while the
# daemon tracks a session of the user), API field -> daemon key
STATUS_SAVED = {
    "time_spent_balance": "TIME_SPENT_BALANCE",
    "time_spent_day": "TIME_SPENT_DAY",
    "time_spent_week": "TIME_SPENT_WEEK",
    "time_spent_month": "TIME_SPENT_MONTH",
    "time_left_day": "TIME_LEFT_DAY",
}
STATUS_LIVE = {
    "time_spent_session": "ACTUAL_TIME_SPENT_SESSION",
    "time_inactive_session": "ACTUAL_TIME_INACTIVE_SESSION",
    "time_spent_balance": "ACTUAL_TIME_SPENT_BALANCE",
    "time_spent_day": "ACTUAL_TIME_SPENT_DAY",
    "time_left_day": "ACTUAL_TIME_LEFT_DAY",
    "time_left_continuous": "ACTUAL_TIME_LEFT_CONTINUOUS",
}

# API operation -> daemon operation for adjusting time left
TIME_LEFT_OPERATIONS = {"add": "+", "subtract": "-", "set": "="}


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


# ## whole resources ##


def _limits_from_daemon(info, fields):
    days = [int(day) for day in info["ALLOWED_WEEKDAYS"]]
    config = {
        "allowed_days": days,
        "limits_per_day": limits_by_day(days, info["LIMITS_PER_WEEKDAYS"]),
        "allowed_hours": {
            day: hours_from_daemon(info[f"ALLOWED_HOURS_{day}"]) for day in WEEKDAYS
        },
    }
    config.update({field: info[key] for field, (key, _setter) in fields.items()})
    return config


def _limits_to_daemon(config):
    """The daemon's keys up to the scalars, in its order"""
    days = config["allowed_days"]
    hours = config["allowed_hours"]
    info = {
        f"ALLOWED_HOURS_{day}": hours_to_daemon(hours.get(str(day), hours.get(day, [])))
        for day in WEEKDAYS
    }
    info["ALLOWED_WEEKDAYS"] = [str(day) for day in days]
    info["LIMITS_PER_WEEKDAYS"] = limits_list(days, config["limits_per_day"])
    return info


def user_config_from_daemon(info):
    return _limits_from_daemon(info, USER_FIELDS)


def user_config_to_daemon(config):
    """The inverse of user_config_from_daemon, in the daemon's key order"""
    info = _limits_to_daemon(config)
    info["TRACK_INACTIVE"] = config["track_inactive"]
    info["HIDE_TRAY_ICON"] = config["hide_tray_icon"]
    info["LIMIT_PER_WEEK"] = config["limit_per_week"]
    info["LIMIT_PER_MONTH"] = config["limit_per_month"]
    return info


def user_policy_from_daemon(info):
    """Where a user's effective policy comes from (the daemon adds these
    keys to a user's full information)"""
    return {
        "policy_source": info["POLICY_SOURCE"],
        "policy_groups": [str(group) for group in info["POLICY_GROUPS"]],
    }


def user_policy_to_daemon(user):
    return {
        "POLICY_SOURCE": user["policy_source"],
        "POLICY_GROUPS": list(user["policy_groups"]),
    }


def group_config_from_daemon(info):
    """A group's policy: the limits, without the tray icon, plus the groups
    it overrides"""
    config = _limits_from_daemon(info, GROUP_FIELDS)
    config["overrides"] = [str(group) for group in info["OVERRIDES"]]
    return config


def group_config_to_daemon(config):
    """The inverse of group_config_from_daemon, in the daemon's key order
    (HIDE_TRAY_ICON, which the daemon returns but which means nothing for a
    group, is left out)"""
    info = _limits_to_daemon(config)
    info["TRACK_INACTIVE"] = config["track_inactive"]
    info["LIMIT_PER_WEEK"] = config["limit_per_week"]
    info["LIMIT_PER_MONTH"] = config["limit_per_month"]
    info["OVERRIDES"] = list(config["overrides"])
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
