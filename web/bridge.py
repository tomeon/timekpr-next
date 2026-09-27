"""
timekprw: bridge between the web API models and the timekpr daemon.

Every call goes through timekprAdminConnector, the same D-Bus client that
timekpra and the GTK administration tool use, so the daemon's own
validation applies unchanged.  The value conversions live in
timekpr.common.utils.webapi (shared with timekpra's HTTP connector);
this module adds the Pydantic models, the daemon's error conventions,
and how a PATCH is applied: a policy's in one call the daemon applies
whole or not at all (applyPolicyChanges), the daemon-wide settings one
setter at a time.
"""

import gettext
import os
import re
import threading
import time

import dbus

from timekpr.common.constants import constants as cons
from timekpr.common.constants import messages as msg
from timekpr.common.utils import webapi
from timekpr.web import models

# the daemon reports "not connected" and "call failed" through these codes
_RESULT_NOT_READY = -2
# the daemon's replies when it hit an exception rather than invalid input
_DAEMON_FAILURES = (
    "TK_MSG_CONFIG_LOADER_UNEXPECTED_ERROR",
    "TK_MSG_CONFIG_LOADER_USER_UNEXPECTED_ERROR",
    "TK_MSG_CONFIG_LOADER_USERLIST_UNEXPECTED_ERROR",
    "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR",
    "TK_MSG_CONFIG_LOADER_SAVECONTROL_UNEXPECTED_ERROR",
)
# the daemon's reply when it cannot answer right now (NSS could not say
# which groups the user is in)
_DAEMON_UNAVAILABLE = ("TK_MSG_CONFIG_LOADER_USER_LOOKUP_FAILED",)
# the daemon's replies when what the request names does not exist
_DAEMON_NOT_FOUND = (
    "TK_MSG_CONFIG_LOADER_USER_NOTFOUND",
    "TK_MSG_CONFIG_LOADER_GROUPCONFIG_NOTFOUND",
    "TK_MSG_CONFIG_LOADER_POLICY_NOTFOUND",
    "TK_MSG_CONFIG_LOADER_SETTING_NOTSET",
)
_daemon_texts = {}


def daemon_texts(keys):
    """The daemon's messages for the given keys in every locale timekpr
    ships, as they come back with a -1 result.  The daemon only reports a
    message translated in its own locale, which need not be ours, so all
    translations are recognized.  Messages with a placeholder are turned
    into regular expressions."""
    if keys not in _daemon_texts:
        patterns = set()
        try:
            languages = os.listdir(cons.TK_LOCALIZATION_DIR)
        except OSError:
            languages = []
        for key in keys:
            source = msg._messages[key]["s"]
            texts = {source, msg.getTranslation(key)}
            for language in languages:
                try:
                    texts.add(
                        gettext.translation(
                            "timekpr", cons.TK_LOCALIZATION_DIR, languages=[language]
                        ).gettext(source)
                    )
                except OSError:
                    pass
            for text in texts:
                # the catalogs hold "%%s", getTranslation already turned it
                # into "%s", and the daemon formats it into the name
                text = text.replace("%%", "%")
                patterns.add(
                    "^" + ".*".join(re.escape(part) for part in text.split("%s")) + "$"
                )
        _daemon_texts[keys] = re.compile("|".join(patterns), re.DOTALL)
    return _daemon_texts[keys]


class DaemonError(Exception):
    """A request the daemon (or the connection to it) refused"""

    def __init__(self, status, detail, field=None, applied=(), payload=None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.field = field
        self.applied = list(applied)
        # what the daemon returned besides its message
        self.payload = payload


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


class Bridge:
    """Serialized access to the daemon through timekprAdminConnector"""

    # /health needs no token, so its daemon round trip is rate limited by
    # remembering the answer for this long
    HEALTH_CACHE_SECONDS = 5

    def __init__(self, connector=None):
        self._connector = connector
        self._lock = threading.Lock()
        self._health = (0, None)

    def _connect(self):
        """Return a connected connector or raise DaemonError"""
        if self._connector is None:
            # importing dbus-related modules is deferred so that the API
            # models can be used without a system bus (tests)
            from timekpr.client.interface.dbus.administration import (
                timekprAdminConnector,
            )

            try:
                self._connector = timekprAdminConnector()
            except dbus.DBusException as ex:
                raise DaemonError(503, f"cannot connect to the system bus: {ex}")
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
        payload = result[2] if len(result) > 2 else None
        if code != 0:
            raise DaemonError(
                _status(code, message, connected), message, payload=payload
            )
        return payload

    # ## service ##

    def health(self):
        checked, result = self._health
        if result is None or time.monotonic() - checked > self.HEALTH_CACHE_SECONDS:
            try:
                self._call("getUserList")
                result = models.Health(daemon="ok", timekpr_version=cons.TK_VERSION)
            except DaemonError:
                result = models.Health(
                    daemon="unreachable", timekpr_version=cons.TK_VERSION
                )
            self._health = (time.monotonic(), result)
        return result

    def get_server_config(self):
        return models.ServerConfig(
            **webapi.server_config_from_daemon(self._call("getTimekprConfiguration"))
        )

    def patch_server_config(self, patch):
        apply_scalars(Steps(self), "", patch, webapi.SERVER_FIELDS)
        return self.get_server_config()

    # ## users ##

    def list_users(self, include_status=False):
        users = [
            models.UserSummary(
                username=user[0], full_name=user[1], policy_source=user[2]
            )
            for user in self._call("getUserList")
        ]
        if include_status:
            for user in users:
                user.status = self.get_user_status(user.username)
        return users

    def _user_info(self, username, level):
        """The daemon answers the effective policy of every user it or NSS
        knows (a directory user need not be listed), and "not found" for
        any other name, which _call turns into a 404"""
        return self._call("getUserConfigurationAndInformation", username, level)

    def get_user(self, username):
        info = self._user_info(username, cons.TK_CL_INF_FULL)
        return models.User(
            username=username,
            config=models.UserConfig(**webapi.user_config_from_daemon(info)),
            status=models.UserStatus(**webapi.user_status_from_daemon(info)),
            **webapi.user_policy_from_daemon(info),
        )

    def get_user_config(self, username):
        return models.UserConfig(
            **webapi.user_config_from_daemon(
                self._user_info(username, cons.TK_CL_INF_FULL)
            )
        )

    def get_user_status(self, username):
        return models.UserStatus(
            **webapi.user_status_from_daemon(
                self._user_info(username, cons.TK_CL_INF_FULL)
            )
        )

    def _change_policy(self, target, patch, fields):
        """A PATCH of a policy, as one change the daemon applies whole or
        not at all; a refusal names the field it was about"""
        unset, changes = policy_changes(patch, fields)
        try:
            self._call("applyPolicyChanges", target, unset, changes)
        except DaemonError as ex:
            raise DaemonError(ex.status, ex.detail, patch_field(ex.payload)) from ex

    def patch_user_config(self, username, patch):
        self._change_policy(username, patch, webapi.USER_FIELDS)
        return self.get_user_config(username)

    def set_allowed_hours(self, username, day, entries):
        """day is an ISO weekday or "all" """
        Steps(self, username).run(
            f"allowed_hours.{day}",
            "setAllowedHours",
            "ALL" if day == "all" else str(day),
            webapi.hours_to_daemon([entry.model_dump() for entry in entries]),
        )
        return self.get_user_config(username)

    def unset_allowed_hours(self, username, day):
        """Take one day's (or every day's) hours out of the user's policy"""
        Steps(self, username).run(
            f"allowed_hours.{day}", "unsetSetting", hours_setting(day)
        )
        return self.get_user_config(username)

    def set_time_left(self, username, request):
        self._call(
            "setTimeLeft",
            username,
            webapi.TIME_LEFT_OPERATIONS[request.operation],
            request.seconds,
        )
        return self.get_user_status(username)

    # ## policies ##

    def _delete_policy(self, target, what):
        # a policy that is not there to delete is a 404 (see _call)
        self._call("deletePolicy", target)

    def delete_user_policy(self, username):
        """The user's group policies (or the defaults) apply again; the
        counters stay"""
        self._delete_policy(username, f"user {username}")

    def migrate_policies(self, request):
        return models.MigrationResult(
            users=self._call("migratePolicies", request.dry_run)
        )

    # ## groups ##

    def list_groups(self):
        return [
            models.GroupSummary(
                group=group[0],
                overrides=split_list(group[1]),
                members=split_list(group[2]),
            )
            for group in self._call("getGroupList")
        ]

    def _group_info(self, group):
        # a group without a policy is a 404 (see _call)
        return self._call(
            "getUserConfigurationAndInformation",
            group_target(group),
            cons.TK_CL_INF_FULL,
        )

    def get_group(self, group):
        info = self._group_info(group)
        return models.Group(
            group=group,
            config=models.GroupConfig(**webapi.group_config_from_daemon(info)),
            policy_settings=webapi.policy_settings_from_daemon(info),
        )

    def get_group_config(self, group):
        return models.GroupConfig(
            **webapi.group_config_from_daemon(self._group_info(group))
        )

    def patch_group_config(self, group, patch):
        """A group without a policy gets one, even from an empty PATCH (the
        daemon creates it, with the defaults for what it does not set)"""
        self._change_policy(group_target(group), patch, webapi.GROUP_FIELDS)
        return self.get_group_config(group)

    def unset_group_allowed_hours(self, group, day):
        """Take one day's (or every day's) hours out of the group's policy"""
        Steps(self, group_target(group)).run(
            f"allowed_hours.{day}", "unsetSetting", hours_setting(day)
        )
        return self.get_group_config(group)

    def set_group_allowed_hours(self, group, day, entries):
        """day is an ISO weekday or "all"; the policy is created if needed"""
        Steps(self, group_target(group)).run(
            f"allowed_hours.{day}",
            "setAllowedHours",
            "ALL" if day == "all" else str(day),
            webapi.hours_to_daemon([entry.model_dump() for entry in entries]),
        )
        return self.get_group_config(group)

    def delete_group_policy(self, group):
        self._delete_policy(group_target(group), f"group {group}")


def group_target(group):
    """How the daemon addresses a group wherever it takes a user name"""
    return cons.TK_GROUP_TARGET_PREFIX + group


def split_list(value):
    """The daemon's ";"-joined lists (an empty string is an empty list)"""
    return [item for item in value.split(";") if item != ""]


def _status(code, message, connected):
    """The HTTP status for a daemon result other than 0"""
    if code == _RESULT_NOT_READY or not connected:
        return 503
    if message.startswith(
        msg.getTranslation("TK_MSG_DBUS_COMMUNICATION_COMMAND_FAILED")
    ):
        # the daemon (through polkit) refused us: timekprw's user is not in
        # the timekpr group; the connector appends the daemon's reason to
        # its own message, which is in our locale
        return 502
    if daemon_texts(_DAEMON_FAILURES).match(message):
        # the daemon could not apply a valid request (its log has the reason,
        # a read-only /etc/timekpr for example)
        return 500
    if daemon_texts(_DAEMON_UNAVAILABLE).match(message):
        # the daemon could not resolve the user's policy (the directory
        # did not answer); try again later
        return 503
    if daemon_texts(_DAEMON_NOT_FOUND).match(message):
        # what the request names does not exist: a user nobody knows, a
        # group without a policy, a policy that is not there to delete
        return 404
    return 400


def policy_changes(patch, fields):
    """A PATCH of a policy as applyPolicyChanges takes it: the settings to
    take out (the fields sent as null; the allowed days and their limits
    are one setting, allowed_hours is every day's hours) and the ones to
    set, by the daemon's setting names (which are the fields', one
    allowed_hours_N per day).  The daemon applies the nulls first and
    aligns the limits with the allowed days itself."""
    unset = []
    for field in type(patch).model_fields:
        if field in patch.model_fields_set and getattr(patch, field) is None:
            setting = "allowed_days" if field == "limits_per_day" else field
            if setting not in unset:
                unset.append(setting)
    changes = {}
    if patch.allowed_days is not None:
        changes["allowed_days"] = [str(day) for day in patch.allowed_days]
    if patch.limits_per_day is not None:
        changes["limits_per_day"] = {
            str(day): seconds for day, seconds in patch.limits_per_day.items()
        }
    for day, entries in (patch.allowed_hours or {}).items():
        changes[f"allowed_hours_{day}"] = webapi.hours_to_daemon(
            [entry.model_dump() for entry in entries]
        )
    for field in fields:
        if getattr(patch, field) is not None:
            changes[field] = getattr(patch, field)
    if getattr(patch, "overrides", None) is not None:
        changes["overrides"] = list(patch.overrides)
    return unset, changes


def patch_field(setting):
    """The PATCH field a setting the daemon refused stands for (None when
    it named none)"""
    if not setting:
        return None
    if setting.startswith("allowed_hours_"):
        return "allowed_hours." + setting[len("allowed_hours_") :]
    return setting


class Steps:
    """Runs setters one by one (a PATCH of the daemon-wide settings, the
    single call of a sub-resource), remembering which fields were written
    so that a failure can report them"""

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


def hours_setting(day):
    """The daemon's name for one day's hours, or every day's"""
    return "allowed_hours" if day == "all" else f"allowed_hours_{day}"


def apply_scalars(steps, prefix, patch, fields):
    for field, (_key, setter) in fields.items():
        value = getattr(patch, field)
        if value is not None:
            steps.run(prefix + field, setter, value)
