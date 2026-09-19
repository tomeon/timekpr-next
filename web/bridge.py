"""
timekprw: bridge between the web API models and the timekpr daemon.

Every call goes through timekprAdminConnector, the same D-Bus client that
timekpra and the GTK administration tool use, so the daemon's own
validation applies unchanged.  The value conversions live in
timekpr.common.utils.webapi (shared with timekpra's HTTP connector);
this module adds the Pydantic models, the daemon's error conventions,
and the order in which a PATCH is applied.
"""

import gettext
import os
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
_daemon_failure_texts = None


def daemon_failure_texts():
    """The daemon's failure messages in every locale timekpr ships.  The
    daemon only reports -1 and a message translated in its own locale, which
    need not be ours, so all translations are recognized."""
    global _daemon_failure_texts
    if _daemon_failure_texts is None:
        texts = set()
        try:
            languages = os.listdir(cons.TK_LOCALIZATION_DIR)
        except OSError:
            languages = []
        for key in _DAEMON_FAILURES:
            source = msg._messages[key]["s"]
            texts.update({source, msg.getTranslation(key)})
            for language in languages:
                try:
                    texts.add(
                        gettext.translation(
                            "timekpr", cons.TK_LOCALIZATION_DIR, languages=[language]
                        ).gettext(source)
                    )
                except OSError:
                    pass
        _daemon_failure_texts = texts
    return _daemon_failure_texts


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
        if code == _RESULT_NOT_READY or (code != 0 and not connected):
            raise DaemonError(503, message)
        if code != 0 and message.startswith(
            msg.getTranslation("TK_MSG_DBUS_COMMUNICATION_COMMAND_FAILED")
        ):
            # the daemon (through polkit) refused us: timekprw's user is not in
            # the timekpr group; the connector appends the daemon's reason to
            # its own message, which is in our locale
            raise DaemonError(502, message)
        if code != 0 and message in daemon_failure_texts():
            # the daemon could not apply a valid request (its log has the reason,
            # a read-only /etc/timekpr for example)
            raise DaemonError(500, message)
        if code != 0:
            raise DaemonError(400, message)
        return result[2] if len(result) > 2 else None

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

    def _require_user(self, username):
        """The daemon answers an effective policy for any name; only the
        users it lists (those with a policy, present in the system, or
        known members of a group with a policy) are resources here"""
        if username not in [user[0] for user in self._call("getUserList")]:
            raise DaemonError(404, f"timekpr has no configuration for user {username}")

    def _user_info(self, username, level):
        self._require_user(username)
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

    def patch_user_config(self, username, patch):
        current = self.get_user_config(username)
        steps = Steps(self, username)
        apply_days_and_limits(
            steps, "", patch, current, "setAllowedDays", "setTimeLimitForDays"
        )
        if patch.allowed_hours is not None:
            for day, entries in patch.allowed_hours.items():
                steps.run(
                    f"allowed_hours.{day}",
                    "setAllowedHours",
                    str(day),
                    webapi.hours_to_daemon([entry.model_dump() for entry in entries]),
                )
        apply_scalars(steps, "", patch, webapi.USER_FIELDS)
        return self.get_user_config(username)

    def set_allowed_hours(self, username, day, entries):
        """day is an ISO weekday or "all" """
        self._require_user(username)
        Steps(self, username).run(
            f"allowed_hours.{day}",
            "setAllowedHours",
            "ALL" if day == "all" else str(day),
            webapi.hours_to_daemon([entry.model_dump() for entry in entries]),
        )
        return self.get_user_config(username)

    def set_time_left(self, username, request):
        self._require_user(username)
        self._call(
            "setTimeLeft",
            username,
            webapi.TIME_LEFT_OPERATIONS[request.operation],
            request.seconds,
        )
        return self.get_user_status(username)

    # ## policies ##

    def _delete_policy(self, target, what):
        try:
            self._call("deletePolicy", target)
        except DaemonError as ex:
            # the only request the daemon refuses here is deleting a policy
            # that does not exist
            if ex.status != 400:
                raise
            raise DaemonError(404, f"timekpr has no policy for {what}")

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

    def _require_group(self, group):
        if group not in [entry[0] for entry in self._call("getGroupList")]:
            raise DaemonError(404, f"timekpr has no policy for group {group}")

    def _group_info(self, group):
        self._require_group(group)
        return self._call(
            "getUserConfigurationAndInformation",
            group_target(group),
            cons.TK_CL_INF_FULL,
        )

    def get_group(self, group):
        return models.Group(group=group, config=self.get_group_config(group))

    def get_group_config(self, group):
        return models.GroupConfig(
            **webapi.group_config_from_daemon(self._group_info(group))
        )

    def patch_group_config(self, group, patch):
        """A group without a policy gets one from its first setter, so the
        current values a PATCH is applied against are then the daemon's
        defaults for a new policy"""
        try:
            current = self.get_group_config(group)
        except DaemonError as ex:
            if ex.status != 404:
                raise
            # no policy yet: have the daemon create one with its defaults
            # (any setter does; an empty override list changes nothing), so
            # that even an empty PATCH creates the policy
            Steps(self, group_target(group)).run("overrides", "setOverrides", [])
            current = self.get_group_config(group)
        steps = Steps(self, group_target(group))
        apply_days_and_limits(
            steps, "", patch, current, "setAllowedDays", "setTimeLimitForDays"
        )
        if patch.allowed_hours is not None:
            for day, entries in patch.allowed_hours.items():
                steps.run(
                    f"allowed_hours.{day}",
                    "setAllowedHours",
                    str(day),
                    webapi.hours_to_daemon([entry.model_dump() for entry in entries]),
                )
        apply_scalars(steps, "", patch, webapi.GROUP_FIELDS)
        if patch.overrides is not None:
            steps.run("overrides", "setOverrides", list(patch.overrides))
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


class Steps:
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


def apply_scalars(steps, prefix, patch, fields):
    for field, (_key, setter) in fields.items():
        value = getattr(patch, field)
        if value is not None:
            steps.run(prefix + field, setter, value)


def apply_days_and_limits(steps, prefix, patch, current, days_setter, limits_setter):
    """Allowed days and their limits are coupled: the daemon stores limits
    positionally against the allowed days, so whenever either changes the
    limits are re-sent aligned with the (new) allowed days"""
    days = (
        sorted(set(patch.allowed_days))
        if patch.allowed_days is not None
        else current.allowed_days
    )
    if patch.allowed_days is not None:
        steps.run(prefix + "allowed_days", days_setter, [str(day) for day in days])
    if patch.allowed_days is not None or patch.limits_per_day is not None:
        limits = {**current.limits_per_day, **(patch.limits_per_day or {})}
        steps.run(
            prefix + "limits_per_day", limits_setter, webapi.limits_list(days, limits)
        )
