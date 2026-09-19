"""
timekpra: administration connector that talks to timekprw over HTTP
instead of to the daemon over D-Bus.

It offers the same methods, arguments and (result, message[, payload])
results as timekpr.client.interface.dbus.administration, so the admin
client can use either.  Payloads come back in the daemon's shapes,
converted with timekpr.common.utils.webapi.
"""

import http.client
import json
import os
import socket
from urllib.parse import quote, urlsplit

from timekpr.common.constants import constants as cons
from timekpr.common.constants import messages as msg
from timekpr.common.log import log
from timekpr.common.utils import webapi

API_PREFIX = "/api/v1"
DAEMON_OPERATIONS = {daemon: api for api, daemon in webapi.TIME_LEFT_OPERATIONS.items()}


class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTP over a UNIX domain socket"""

    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def is_group(target):
    """Whether a target names a group (@group) rather than a user"""
    return len(target) > 1 and target.startswith(cons.TK_GROUP_TARGET_PREFIX)


def group_name(target):
    """The group a target names (a plain name is returned unchanged)"""
    if is_group(target):
        return target[len(cons.TK_GROUP_TARGET_PREFIX) :]
    return target


def user_path(username, suffix=""):
    return "/users/{}{}".format(quote(username, safe=""), suffix)


def group_path(group, suffix=""):
    return "/groups/{}{}".format(quote(group, safe=""), suffix)


def target_path(target, suffix=""):
    """The resource of a user or of a group (@group)"""
    if is_group(target):
        return group_path(group_name(target), suffix)
    return user_path(target, suffix)


class timekprAdminHttpConnector:
    """Connector to timekprw at http://HOST:PORT[/PREFIX], https://... or unix:///PATH"""

    def __init__(self, url, tokenFile=None, timeout=30):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https", "unix"):
            raise ValueError(
                f"unsupported server URL {url} (use http://, https:// or unix://)"
            )
        self._url = url
        self._scheme = parts.scheme
        self._netloc = parts.netloc
        # unix:///run/x.sock and unix:/run/x.sock name an absolute path
        self._path = (
            (f"/{parts.netloc}{parts.path}")
            if parts.scheme == "unix" and parts.netloc
            else parts.path
        )
        self._prefix = (
            "" if parts.scheme == "unix" else parts.path.rstrip("/")
        ) + API_PREFIX
        self._timeout = timeout
        self._token = self._readToken(tokenFile)
        self._connected = False
        self._initFailed = False

    def _readToken(self, tokenFile):
        """An explicitly given token file is always used; otherwise the
        default one, if it exists, for TCP (timekprw trusts its UNIX
        sockets unless run with --auth-unix)"""
        path = tokenFile if tokenFile is not None else cons.TK_WEB_TOKEN_FILE
        if tokenFile is None and (self._scheme == "unix" or not os.path.exists(path)):
            return None
        with open(path, "r") as tokenFileHandle:
            return tokenFileHandle.read().strip()

    # ## transport ##

    def _connection(self):
        if self._scheme == "unix":
            return _UnixHTTPConnection(self._path, self._timeout)
        if self._scheme == "https":
            return http.client.HTTPSConnection(self._netloc, timeout=self._timeout)
        return http.client.HTTPConnection(self._netloc, timeout=self._timeout)

    def _request(self, method, path, body=None, headers=None):
        """Return (HTTP status, decoded JSON body or None); raises OSError / ValueError"""
        headers = dict(headers or {}, Accept="application/json")
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection = self._connection()
        try:
            connection.request(
                method,
                self._prefix + path,
                body=None if body is None else json.dumps(body),
                headers=headers,
            )
            response = connection.getresponse()
            data = response.read()
        finally:
            connection.close()
        return response.status, (json.loads(data) if data else None)

    def _call(self, method, path, body=None):
        """Return (result, message, payload) in the D-Bus connector's convention"""
        try:
            status, data = self._request(method, path, body)
        except (OSError, ValueError) as ex:
            log.log(cons.TK_LOG_LEVEL_INFO, f'ERROR: "{ex}" in "{method} {path}"')
            self._connected = False
            return -1, f"FAILED to reach timekprw at {self._url}: {ex}", None
        if status >= 400:
            problem = data if isinstance(data, dict) else {}
            message = problem.get("detail") or problem.get("title") or f"HTTP {status}"
            errors = [
                "{}: {}".format(error["field"], error["message"])
                for error in problem.get("errors", [])
                if error["message"] != message
            ]
            return (
                -1,
                message + (" ({})".format("; ".join(errors)) if errors else ""),
                None,
            )
        return 0, "", data

    # ## the D-Bus connector's interface ##

    def initTimekprConnection(self, pTryOnce, pRescheduleConnection=False):
        """Check that timekprw answers (whether or not the daemon behind it does)"""
        try:
            self._request("GET", "/health")
            self._connected = True
            self._initFailed = False
        except (OSError, ValueError) as ex:
            self._connected = False
            self._initFailed = True
            log.consoleOut(
                f"FAILED to reach timekprw at {self._url}: {ex}\nPlease check that timekprw is running and the URL is right"
            )

    def isConnected(self):
        """Return status of connection, in the D-Bus connector's shape"""
        return self._connected, not self._initFailed

    def getUserList(self):
        result, message, users = self._call("GET", "/users")
        return (
            result,
            message,
            [
                [user["username"], user["full_name"], user["policy_source"]]
                for user in users or []
            ],
        )

    def getUserConfigurationAndInformation(self, pUserName, pInfoLvl):
        # a group's policy has no counters
        if is_group(pUserName):
            result, message, group = self._call("GET", target_path(pUserName))
            if result != 0:
                return result, message, {}
            return (
                result,
                message,
                webapi.group_config_to_daemon(group["config"])
                if pInfoLvl == cons.TK_CL_INF_FULL
                else {},
            )
        result, message, user = self._call("GET", user_path(pUserName))
        if result != 0:
            return result, message, {}
        info = {}
        if pInfoLvl == cons.TK_CL_INF_FULL:
            info.update(webapi.user_config_to_daemon(user["config"]))
            info.update(webapi.user_policy_to_daemon(user))
        info.update(
            webapi.user_status_to_daemon(
                user["status"],
                saved=pInfoLvl != cons.TK_CL_INF_RT,
                live=pInfoLvl != cons.TK_CL_INF_SAVED,
            )
        )
        return result, message, info

    def getGroupList(self):
        result, message, groups = self._call("GET", "/groups")
        return (
            result,
            message,
            [
                [
                    group["group"],
                    ";".join(group["overrides"]),
                    ";".join(group["members"]),
                ]
                for group in groups or []
            ],
        )

    def getTimekprConfiguration(self):
        result, message, config = self._call("GET", "/config")
        return (
            result,
            message,
            webapi.server_config_to_daemon(config) if result == 0 else {},
        )

    def _usersOnly(self, pUserName):
        """The daemon's answer to a per-user setting for a group"""
        return -1, msg.getTranslation("TK_MSG_USER_ADMIN_CHK_GROUP_NOT_USER") % (
            group_name(pUserName)
        )

    def _groupsOnly(self, pUserName):
        """The daemon's answer to a per-group setting for a user"""
        return -1, msg.getTranslation("TK_MSG_USER_ADMIN_CHK_USER_NOT_GROUP") % (
            pUserName
        )

    def _patchUser(self, pUserName, body):
        # the one per-user setting a group policy does not have
        if is_group(pUserName) and "hide_tray_icon" in body:
            return self._usersOnly(pUserName)
        return self._call("PATCH", target_path(pUserName, "/config"), body)[:2]

    def _userConfig(self, pUserName):
        result, message, config = self._call("GET", target_path(pUserName, "/config"))
        return result, message, config or {}

    def setAllowedDays(self, pUserName, pDayList):
        return self._patchUser(
            pUserName, {"allowed_days": [int(day) for day in pDayList]}
        )

    def setAllowedHours(self, pUserName, pDayNumber, pHourList):
        day = "all" if pDayNumber == "ALL" else str(pDayNumber)
        return self._call(
            "PUT",
            target_path(pUserName, f"/config/allowed-hours/{day}"),
            webapi.hours_from_daemon(pHourList),
        )[:2]

    def setTimeLimitForDays(self, pUserName, pDayLimits):
        """Limits are positional against the allowed days, as for the daemon"""
        result, message, config = self._userConfig(pUserName)
        if result != 0 and is_group(pUserName):
            # a group without a policy has no config to read; the daemon
            # creates the policy with every day allowed on the first setting
            config = {"allowed_days": webapi.WEEKDAYS}
        elif result != 0:
            return result, message
        return self._patchUser(
            pUserName,
            {
                "limits_per_day": webapi.limits_by_day(
                    config["allowed_days"], [int(limit) for limit in pDayLimits]
                )
            },
        )

    def setTimeLeft(self, pUserName, pOperation, pTimeLeft):
        if is_group(pUserName):
            return self._usersOnly(pUserName)
        return self._call(
            "POST",
            user_path(pUserName, "/time-left"),
            {"operation": DAEMON_OPERATIONS[pOperation], "seconds": int(pTimeLeft)},
        )[:2]

    def setOverrides(self, pUserName, pOverrides):
        if not is_group(pUserName):
            return self._groupsOnly(pUserName)
        return self._patchUser(
            pUserName, {"overrides": [group_name(str(group)) for group in pOverrides]}
        )

    def deletePolicy(self, pUserName):
        return self._call("DELETE", target_path(pUserName, "/policy"))[:2]

    def migratePolicies(self, pDryRun):
        result, message, migrated = self._call(
            "POST", "/policies/migrate", {"dry_run": bool(pDryRun)}
        )
        return result, message, migrated["users"] if result == 0 else []


# the scalar setters are one PATCH each; generate them from the field tables
def _userSetter(field):
    return lambda self, pUserName, value: self._patchUser(pUserName, {field: value})


def _serverSetter(field):
    return lambda self, value: self._call("PATCH", "/config", {field: value})[:2]


for _field, (_key, _setter) in webapi.USER_FIELDS.items():
    setattr(timekprAdminHttpConnector, _setter, _userSetter(_field))
for _field, (_key, _setter) in webapi.SERVER_FIELDS.items():
    setattr(timekprAdminHttpConnector, _setter, _serverSetter(_field))
