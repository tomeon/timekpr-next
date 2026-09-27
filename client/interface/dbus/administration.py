"""
Created on Aug 28, 2018

@author: mjasnik
"""

# import
import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

# timekpr imports
from timekpr.common.constants import constants as cons
from timekpr.common.constants import messages as msg
from timekpr.common.log import log
from timekpr.common.utils import misc
from timekpr.common.utils.config import POLICY_CHANGE_SIGNATURES

# default loop
DBusGMainLoop(set_as_default=True)


def _dbusValue(pSignature, pValue):
    """A value as the D-Bus type the signature names (strings, 32-bit
    integers, booleans, and arrays and dictionaries of them), so that an
    empty list or map still has a type"""
    if pSignature == "s":
        return dbus.String(str(pValue))
    if pSignature == "i":
        return dbus.Int32(pValue)
    if pSignature == "b":
        return dbus.Boolean(pValue)
    if pSignature.startswith("a{"):
        keyType, valueType = pSignature[2], pSignature[3:-1]
        return dbus.Dictionary(
            {
                _dbusValue(keyType, rKey): _dbusValue(valueType, rValue)
                for rKey, rValue in pValue.items()
            },
            signature=pSignature[2:-1],
        )
    if pSignature.startswith("a"):
        return dbus.Array(
            [_dbusValue(pSignature[1:], rValue) for rValue in pValue],
            signature=pSignature[1:],
        )
    raise ValueError(f"unsupported D-Bus signature {pSignature!r}")


class timekprAdminConnector:
    """Main class for supporting indicator notifications"""

    def __init__(self):
        """Initialize stuff for connecting to timekpr server"""
        # times
        self._retryTimeoutSecs = 3
        self._retryCountLeft = 5
        self._initFailed = False

        # dbus (timekpr), the bus itself is connected to lazily, so that constructing this does not need a running bus
        self._timekprBus = None
        self._timekprObject = None
        self._timekprUserAdminDbusInterface = None
        self._timekprAdminDbusInterface = None

    def initTimekprConnection(self, pTryOnce, pRescheduleConnection=False):
        """Init dbus (connect to timekpr for info)"""
        # reschedule
        if pRescheduleConnection:
            # rescheduling means dropping existing state and try again
            self._timekprObject = None
            self._timekprUserAdminDbusInterface = None
            self._timekprAdminDbusInterface = None
            self._retryCountLeft = 5
            self._initFailed = False

        # only if notifications are ok
        if self._timekprObject is None:
            try:
                # dbus performance measurement
                misc.measureDBUSTimeElapsed(pStart=True)
                # connect to the bus, if that is not done yet
                if self._timekprBus is None:
                    self._timekprBus = (
                        dbus.SessionBus()
                        if (cons.TK_DEV_ACTIVE and cons.TK_DEV_BUS == "ses")
                        else dbus.SystemBus()
                    )
                # timekpr connection stuff
                self._timekprObject = self._timekprBus.get_object(
                    cons.TK_DBUS_BUS_NAME, cons.TK_DBUS_SERVER_PATH
                )
                # measurement logging
                misc.measureDBUSTimeElapsed(
                    pStop=True, pPrintToConsole=True, pDbusIFName=cons.TK_DBUS_BUS_NAME
                )
            except Exception:
                self._timekprObject = None
                # logging
                log.consoleOut(
                    "FAILED to obtain connection to timekpr.\nPlease check that timekpr daemon is working and you have sufficient permissions to access it (either superuser or timekpr group)"
                )

            # only if notifications are ok
        if (
            self._timekprObject is not None
            and self._timekprUserAdminDbusInterface is None
        ):
            try:
                # dbus performance measurement
                misc.measureDBUSTimeElapsed(pStart=True)
                # getting interface
                self._timekprUserAdminDbusInterface = dbus.Interface(
                    self._timekprObject, cons.TK_DBUS_USER_ADMIN_INTERFACE
                )
                # measurement logging
                misc.measureDBUSTimeElapsed(
                    pStop=True,
                    pPrintToConsole=True,
                    pDbusIFName=cons.TK_DBUS_USER_ADMIN_INTERFACE,
                )
            except Exception:
                self._timekprUserAdminDbusInterface = None
                # logging
                log.consoleOut(
                    "FAILED to connect to timekpr user admin interface.\nPlease check that timekpr daemon is working and you have sufficient permissions to access it (either superuser or timekpr group)"
                )

            # only if notifications are ok
        if self._timekprObject is not None and self._timekprAdminDbusInterface is None:
            try:
                # dbus performance measurement
                misc.measureDBUSTimeElapsed(pStart=True)
                # getting interface
                self._timekprAdminDbusInterface = dbus.Interface(
                    self._timekprObject, cons.TK_DBUS_ADMIN_INTERFACE
                )
                # measurement logging
                misc.measureDBUSTimeElapsed(
                    pStop=True,
                    pPrintToConsole=True,
                    pDbusIFName=cons.TK_DBUS_ADMIN_INTERFACE,
                )
            except Exception:
                self._timekprAdminDbusInterface = None
                # logging
                log.consoleOut(
                    "FAILED to connect to timekpr user admin interface.\nPlease check that timekpr daemon is working and you have sufficient permissions to access it (either superuser or timekpr group)"
                )

        # if either of this fails, we keep trying to connect
        if (
            self._timekprUserAdminDbusInterface is None
            or self._timekprAdminDbusInterface is None
        ):
            if self._retryCountLeft > 0 and not pTryOnce:
                log.consoleOut(
                    f"connection failed, {int(self._retryCountLeft)} attempts left, will retry in {int(self._retryTimeoutSecs)} seconds"
                )
                self._retryCountLeft -= 1

                # if either of this fails, we keep trying to connect
                GLib.timeout_add_seconds(3, self.initTimekprConnection, pTryOnce)
            else:
                # failed
                self._initFailed = True

    # --------------- helper methods --------------- #

    def isConnected(self):
        """Return status of connection to DBUS"""
        # if either of this fails, we keep trying to connect
        return not (
            self._timekprUserAdminDbusInterface is None
            or self._timekprAdminDbusInterface is None
        ), not self._initFailed

    def formatException(self, pExceptionStr, pFPath, pFName):
        """Format exception and pass it back"""
        # check for permission error
        if "org.freedesktop.DBus.Error.AccessDenied" in pExceptionStr:
            result = -1
            message = msg.getTranslation("TK_MSG_DBUS_COMMUNICATION_COMMAND_FAILED")
            # the server says why (the text after the error name)
            if ": " in pExceptionStr:
                message = "{} ({})".format(message, pExceptionStr.split(": ", 1)[1])
        else:
            result = -1
            message = msg.getTranslation("TK_MSG_UNEXPECTED_ERROR") % (
                f'"{pExceptionStr}" in "{pFPath}.{pFName}"'
            )
        # log error
        log.log(
            cons.TK_LOG_LEVEL_INFO,
            f'ERROR: "{pExceptionStr}" in "{pFPath}.{pFName}"',
        )
        # result
        return result, message

    def initReturnCodes(self, pInit, pCall):
        """Initialize the return codes for calls"""
        return -2 if pInit else -1 if pCall else 0, msg.getTranslation(
            "TK_MSG_STATUS_INTERFACE_NOTREADY"
        ) if pInit else msg.getTranslation(
            "TK_MSG_DBUS_COMMUNICATION_COMMAND_NOT_ACCEPTED"
        ) if pCall else ""

    # --------------- user configuration info population methods --------------- #

    def getUserList(self):
        """Get user list from server"""
        # defaults
        result, message = self.initReturnCodes(pInit=True, pCall=False)
        userList = []

        # if we have end-point
        if self._timekprUserAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message, userList = (
                    self._timekprUserAdminDbusInterface.getUserList(
                        timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                    )
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.getUserList.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message, userList

    def getUserConfigurationAndInformation(self, pUserName, pInfoLvl):
        """Get user configuration from server"""
        # defaults
        result, message = self.initReturnCodes(pInit=True, pCall=False)
        userConfig = {}

        # if we have end-point
        if self._timekprUserAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message, userConfig = (
                    self._timekprUserAdminDbusInterface.getUserInformation(
                        pUserName, pInfoLvl, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                    )
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.getUserConfigurationAndInformation.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message, userConfig

    # --------------- user configuration set methods --------------- #

    def setAllowedDays(self, pUserName, pDayList):
        """Set user allowed days"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprUserAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = self._timekprUserAdminDbusInterface.setAllowedDays(
                    pUserName, pDayList, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.setAllowedDays.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def setAllowedHours(self, pUserName, pDayNumber, pHourList):
        """Set user allowed days"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprUserAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = self._timekprUserAdminDbusInterface.setAllowedHours(
                    pUserName, pDayNumber, pHourList, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.setAllowedHours.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def setTimeLimitForDays(self, pUserName, pDayLimits):
        """Set user allowed limit for days"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprUserAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = (
                    self._timekprUserAdminDbusInterface.setTimeLimitForDays(
                        pUserName, pDayLimits, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                    )
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.setTimeLimitForDays.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def setTimeLimitForWeek(self, pUserName, pTimeLimitWeek):
        """Set user allowed limit for week"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprUserAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = (
                    self._timekprUserAdminDbusInterface.setTimeLimitForWeek(
                        pUserName, pTimeLimitWeek, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                    )
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.setTimeLimitForWeek.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def setTimeLimitForMonth(self, pUserName, pTimeLimitMonth):
        """Set user allowed limit for month"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprUserAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = (
                    self._timekprUserAdminDbusInterface.setTimeLimitForMonth(
                        pUserName, pTimeLimitMonth, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                    )
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.setTimeLimitForMonth.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def setTrackInactive(self, pUserName, pTrackInactive):
        """Set user allowed days"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprUserAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = self._timekprUserAdminDbusInterface.setTrackInactive(
                    pUserName, pTrackInactive, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.getUserList.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def setHideTrayIcon(self, pUserName, pHideTrayIcon):
        """Set user allowed days"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprUserAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = self._timekprUserAdminDbusInterface.setHideTrayIcon(
                    pUserName, pHideTrayIcon, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.setHideTrayIcon.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def setTimeLeft(self, pUserName, pOperation, pTimeLeft):
        """Set user time left"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprUserAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = self._timekprUserAdminDbusInterface.setTimeLeft(
                    pUserName, pOperation, pTimeLeft, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.setTimeLeft.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    # --------------- policy methods --------------- #

    def getGroupList(self):
        """Get the groups with a policy from server: [group, overrides, known members]"""
        # defaults
        result, message = self.initReturnCodes(pInit=True, pCall=False)
        groupList = []

        # if we have end-point
        if self._timekprUserAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message, groupList = (
                    self._timekprUserAdminDbusInterface.getGroupList(
                        timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                    )
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.getGroupList.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message, groupList

    def setOverrides(self, pGroupName, pOverrides):
        """Set the groups a group's policy takes precedence over"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprUserAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = self._timekprUserAdminDbusInterface.setOverrides(
                    pGroupName, pOverrides, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.setOverrides.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def unsetSetting(self, pUserName, pSetting):
        """Take a setting out of the policy of a user or a group (@group)"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprUserAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = self._timekprUserAdminDbusInterface.unsetSetting(
                    pUserName, pSetting, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.unsetSetting.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def applyPolicyChanges(self, pUserName, pUnset, pChanges):
        """Change several settings of the policy of a user or a group
        (@group) at once, all or nothing: take the settings named in pUnset
        out, set the ones in pChanges ({setting: value}), named as in
        USER_CONFIG_SETTINGS with values as in POLICY_CHANGE_SIGNATURES;
        the third value names the setting the daemon refused"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)
        refused = ""

        # if we have end-point
        if self._timekprUserAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # the values with their D-Bus types (the daemon refuses a
                # setting it does not know, whatever its type)
                changes = dbus.Dictionary(
                    {
                        str(rSetting): (
                            _dbusValue(POLICY_CHANGE_SIGNATURES[str(rSetting)], rValue)
                            if str(rSetting) in POLICY_CHANGE_SIGNATURES
                            else rValue
                        )
                        for rSetting, rValue in pChanges.items()
                    },
                    signature="sv",
                )
                # call dbus method
                result, message, refused = (
                    self._timekprUserAdminDbusInterface.applyPolicyChanges(
                        pUserName,
                        dbus.Array(
                            [str(rSetting) for rSetting in pUnset], signature="s"
                        ),
                        changes,
                        timeout=cons.TK_DBUS_ADMIN_TIMEOUT,
                    )
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.applyPolicyChanges.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message, refused

    def deletePolicy(self, pUserName):
        """Delete the policy of a user or a group (@group)"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprUserAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = self._timekprUserAdminDbusInterface.deletePolicy(
                    pUserName, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.deletePolicy.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def migratePolicies(self, pDryRun):
        """Delete (or with pDryRun only list) the user policies that restrict nothing"""
        # defaults
        result, message = self.initReturnCodes(pInit=True, pCall=False)
        users = []

        # if we have end-point
        if self._timekprUserAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message, users = (
                    self._timekprUserAdminDbusInterface.migratePolicies(
                        pDryRun, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                    )
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.migratePolicies.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message, users

    # --------------- timekpr configuration info population / set methods --------------- #

    def getTimekprConfiguration(self):
        """Get configuration from server"""
        # defaults
        result, message = self.initReturnCodes(pInit=True, pCall=False)
        timekprConfig = {}

        # if we have end-point
        if self._timekprAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message, timekprConfig = (
                    self._timekprAdminDbusInterface.getTimekprConfiguration(
                        timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                    )
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.getTimekprConfiguration.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message, timekprConfig

    def setTimekprLogLevel(self, pLogLevel):
        """Set the logging level for server"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = self._timekprAdminDbusInterface.setTimekprLogLevel(
                    pLogLevel, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.setTimekprLogLevel.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def setTimekprPollTime(self, pPollTimeSecs):
        """Set polltime for timekpr"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = self._timekprAdminDbusInterface.setTimekprPollTime(
                    pPollTimeSecs, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.setTimekprPollTime.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def setTimekprSaveTime(self, pSaveTimeSecs):
        """Set save time for timekpr"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = self._timekprAdminDbusInterface.setTimekprSaveTime(
                    pSaveTimeSecs, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.setTimekprSaveTime.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def setTimekprTrackInactive(self, pTrackInactive):
        """Set default value for tracking inactive sessions"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = (
                    self._timekprAdminDbusInterface.setTimekprTrackInactive(
                        pTrackInactive, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                    )
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.setTimekprTrackInactive.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def setTimekprTerminationTime(self, pTerminationTimeSecs):
        """Set up user termination time"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = (
                    self._timekprAdminDbusInterface.setTimekprTerminationTime(
                        pTerminationTimeSecs, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                    )
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.setTimekprTerminationTime.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def setTimekprFinalWarningTime(self, pFinalWarningTimeSecs):
        """Set up final warning time for users"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = (
                    self._timekprAdminDbusInterface.setTimekprFinalWarningTime(
                        pFinalWarningTimeSecs, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                    )
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.setTimekprFinalWarningTime.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def setTimekprFinalNotificationTime(self, pFinalNotificationTimeSecs):
        """Set up final notification time for users"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = (
                    self._timekprAdminDbusInterface.setTimekprFinalNotificationTime(
                        pFinalNotificationTimeSecs, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                    )
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.setTimekprFinalNotificationTime.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def setTimekprSessionsCtrl(self, pSessionsCtrl):
        """Set accountable session types for users"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = (
                    self._timekprAdminDbusInterface.setTimekprSessionsCtrl(
                        pSessionsCtrl, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                    )
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.setTimekprSessionsCtrl.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def setTimekprSessionsExcl(self, pSessionsExcl):
        """Set NON-accountable session types for users"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = (
                    self._timekprAdminDbusInterface.setTimekprSessionsExcl(
                        pSessionsExcl, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                    )
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.setTimekprSessionsExcl.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message

    def setTimekprUsersExcl(self, pUsersExcl):
        """Set excluded usernames for timekpr"""
        # initial values
        result, message = self.initReturnCodes(pInit=True, pCall=False)

        # if we have end-point
        if self._timekprAdminDbusInterface is not None:
            # defaults
            result, message = self.initReturnCodes(pInit=False, pCall=True)

            # notify through dbus
            try:
                # call dbus method
                result, message = self._timekprAdminDbusInterface.setTimekprUsersExcl(
                    pUsersExcl, timeout=cons.TK_DBUS_ADMIN_TIMEOUT
                )
            except Exception as ex:
                # exception
                result, message = self.formatException(
                    str(ex), __name__, self.setTimekprUsersExcl.__name__
                )

                # we cannot send notif through dbus, we need to reschedule connecton
                self.initTimekprConnection(False, True)

        # result
        return result, message
