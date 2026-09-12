"""
Created on Aug 28, 2018

@author: mjasnik
"""

# import
import os
from datetime import datetime

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from timekpr.client.interface.speech.espeak import timekprSpeech

# timekpr imports
from timekpr.common.constants import constants as cons
from timekpr.common.constants import messages as msg
from timekpr.common.log import log
from timekpr.common.utils import misc

# default loop
DBusGMainLoop(set_as_default=True)


class timekprNotifications:
    """Main class for supporting indicator notifications, connect to request methods for timekpr and connections to other DBUS modules"""

    def __init__(self, pUserName, pTimekprClientConfig):
        """Initialize notifications"""
        log.log(cons.TK_LOG_LEVEL_INFO, "start init timekpr notifications")

        # uname
        self._userName = pUserName
        self._timekprClientConfig = pTimekprClientConfig

        # notification (to replace itself in case they are incoming fast)
        self._lastNotifId = 0
        self._lastNotifDT = datetime.now()
        self._lastPTNotifId = 0
        self._lastPTNotifDT = datetime.now()

        # session bus
        self._userSessionBus = dbus.SessionBus()
        # timekpr bus
        self._timekprBus = (
            dbus.SessionBus()
            if (cons.TK_DEV_ACTIVE and cons.TK_DEV_BUS == "ses")
            else dbus.SystemBus()
        )

        # DBUS client connections
        # connection types
        self.CL_CONN_TK = "timekpr"
        self.CL_CONN_NOTIF = "notifications"
        self.CL_CONN_SCR = "screensaver"
        # constants
        self.CL_IF = "primary interface"
        self.CL_IFA = "attributes interface"
        self.CL_SI = "signal"
        self.CL_CNT = "retry_count"
        self.CL_DEL = "delay_times"

        # object collection for DBUS connections
        self._dbusConnections = {
            self.CL_CONN_TK: {
                self.CL_IF: None,
                self.CL_IFA: None,
                self.CL_SI: None,
                self.CL_CNT: 999,
                self.CL_DEL: 0,
            },
            self.CL_CONN_NOTIF: {
                self.CL_IF: None,
                self.CL_IFA: None,
                self.CL_SI: None,
                self.CL_CNT: 99,
                self.CL_DEL: 0,
            },
            self.CL_CONN_SCR: {
                self.CL_IF: None,
                self.CL_IFA: None,
                self.CL_SI: None,
                self.CL_CNT: 5,
                self.CL_DEL: 2,
            },
        }

        # WORKAROUNDS section start
        # adjust even bigger delay with unity + 18.04 + HDD
        if "UNITY" in os.getenv("XDG_CURRENT_DESKTOP", "N/A").upper():
            # more delay, race condition with screensaver?
            self._dbusConnections[self.CL_CONN_SCR][self.CL_DEL] += cons.TK_POLLTIME - 1
        # WORKAROUNDS section end

        # speech init
        self._timekprSpeechManager = None

        log.log(cons.TK_LOG_LEVEL_INFO, "finish init timekpr notifications")

    def initClientConnections(self):
        """Init dbus (connect to session bus for notification)"""
        log.log(cons.TK_LOG_LEVEL_DEBUG, "start initClientConnections")

        # speech
        if self._timekprSpeechManager is None:
            # initialize
            self._timekprSpeechManager = timekprSpeech()
            # check if supported, if it is, initialize
            if self._timekprSpeechManager.isSpeechSupported():
                # initialize if supported
                self._timekprSpeechManager.initSpeech()

        # only if notifications are not ok
        if (
            self._dbusConnections[self.CL_CONN_NOTIF][self.CL_IF] is None
            and self._dbusConnections[self.CL_CONN_NOTIF][self.CL_CNT] > 0
            and not self._dbusConnections[self.CL_CONN_NOTIF][self.CL_DEL] > 0
        ):
            # define inames (I hope "revolutionary company" won't sue me for using i in front of variable names)
            iNames = ["org.freedesktop.Notifications"]
            iPaths = ["/org/freedesktop/Notifications"]

            # go through inames
            for idx in range(len(iNames)):
                # go through all possible interfaces
                try:
                    # dbus performance measurement
                    misc.measureDBUSTimeElapsed(pStart=True)
                    # getting interface
                    self._dbusConnections[self.CL_CONN_NOTIF][self.CL_IF] = (
                        dbus.Interface(
                            self._userSessionBus.get_object(iNames[idx], iPaths[idx]),
                            iNames[idx],
                        )
                    )
                    # measurement logging
                    misc.measureDBUSTimeElapsed(pStop=True, pDbusIFName=iNames[idx])

                    # first sucess is enough
                    log.log(
                        cons.TK_LOG_LEVEL_DEBUG,
                        f"CONNECTED to DBUS {self.CL_CONN_NOTIF} interface",
                    )
                    # log
                    log.log(
                        cons.TK_LOG_LEVEL_INFO,
                        f'INFO: connected to notification service through "{iNames[idx]}"',
                    )
                    # check capabilities
                    if (
                        "sound"
                        not in self._dbusConnections[self.CL_CONN_NOTIF][
                            self.CL_IF
                        ].GetCapabilities()
                    ):
                        # notifications w/ sound are not available
                        self._timekprClientConfig.setIsNotificationSoundSupported(False)
                        # log
                        log.log(
                            cons.TK_LOG_LEVEL_INFO,
                            "INFO: notification sound is not supported",
                        )
                    # add a connection to signal
                    self._dbusConnections[self.CL_CONN_NOTIF][self.CL_SI] = (
                        self._userSessionBus.add_signal_receiver(
                            path=iPaths[idx],
                            handler_function=self.receiveNotificationClosed,
                            dbus_interface=iNames[idx],
                            signal_name="NotificationClosed",
                        )
                    )
                    # log
                    log.log(
                        cons.TK_LOG_LEVEL_INFO,
                        f'INFO: connected to notification closing callback service through "{iNames[idx]}"',
                    )
                    # finish
                    break
                except Exception as dbusEx:
                    self._dbusConnections[self.CL_CONN_NOTIF][self.CL_IF] = None
                    # logging
                    log.log(
                        cons.TK_LOG_LEVEL_INFO,
                        f'WARNING: initiating dbus connection ("{__name__}.{self.initClientConnections.__name__}", {self.CL_CONN_NOTIF}, {iNames[idx]}), error: {dbusEx!s}',
                    )

        # only if screensaver is not ok
        if (
            self._dbusConnections[self.CL_CONN_SCR][self.CL_IF] is None
            and self._dbusConnections[self.CL_CONN_SCR][self.CL_CNT] > 0
            and not self._dbusConnections[self.CL_CONN_SCR][self.CL_DEL] > 0
        ):
            # define inames (I hope "revolutionary company" won't sue me for using i in front of variable names :) )
            iNames = []
            iPaths = []
            chosenIdx = None
            currentDE = None
            isGnomeScrUsed = False
            isFDScrUsed = False

            # THIS WHOLE SECTION IS WORKAROUNDS FOR MULTIPLE VARIETIES OF SCREENSAVER IMPLEMENTATIONS - START
            # they must be compatible to freedekstop standard, e.g. have corect naming and at least GetActive method as well as ActiveChanged signal

            # get current DE
            currentDE = os.getenv("XDG_CURRENT_DESKTOP", "N/A")
            # log
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f'INFO: current desktop environment "{currentDE}"',
            )
            # transform
            currentDE = currentDE.lower().replace("x-", "")

            # workarounds per desktop
            for rIdx in range(len(cons.TK_SCR_XDGCD_OVERRIDE)):
                # check desktops
                if cons.TK_SCR_XDGCD_OVERRIDE[rIdx][0] in currentDE:
                    log.log(
                        cons.TK_LOG_LEVEL_INFO,
                        f'INFO: using "{cons.TK_SCR_XDGCD_OVERRIDE[rIdx][1]}" screensaver dbus interface as a workaround',
                    )
                    # use gnome stuff
                    iNames.extend(
                        [f"org.{cons.TK_SCR_XDGCD_OVERRIDE[rIdx][1]}.ScreenSaver"]
                    )
                    iPaths.extend(
                        [f"/org/{cons.TK_SCR_XDGCD_OVERRIDE[rIdx][1]}/ScreenSaver"]
                    )
                    # check if gnome screensaver is used (can it be used as failover?)
                    isGnomeScrUsed = (
                        (cons.TK_SCR_XDGCD_OVERRIDE[rIdx][1] == "gnome")
                        if not isGnomeScrUsed
                        else isGnomeScrUsed
                    )
                    # check if freedesktop screensaver is used (can it be used as failover?)
                    isFDScrUsed = (
                        (cons.TK_SCR_XDGCD_OVERRIDE[rIdx][1] == "freedesktop")
                        if not isFDScrUsed
                        else isFDScrUsed
                    )
                    # first match is enough
                    break

            # in case overrides were found, do not try anything else
            if len(iNames) < 1:
                # try parsing DE to get the most accurate DE name (there are desktop names, like, KDE, X-Cinnamon, XFCE and ubuntu:GNOME, ...)
                if ":" in currentDE:
                    # get desktop so we check for second option after ":"
                    currentDE = currentDE.split(":")[1]

                # add to the list
                if currentDE is not None and currentDE != "":
                    log.log(
                        cons.TK_LOG_LEVEL_INFO,
                        f'INFO: trying to use "{currentDE}" as screensaver dbus object',
                    )
                    # add
                    iNames.extend([f"org.{currentDE}.ScreenSaver"])
                    iPaths.extend([f"/org/{currentDE}/ScreenSaver"])
                    # check if gnome screensaver is used (can it be used as failover?)
                    isGnomeScrUsed = (
                        (currentDE == "gnome") if not isGnomeScrUsed else isGnomeScrUsed
                    )

                # add gnome (most popular)
                if not isGnomeScrUsed:
                    # add default section
                    iNames.extend(["org.gnome.ScreenSaver"])
                    iPaths.extend(["/org/gnome/ScreenSaver"])
                # add freedesktop (almost no one uses this, except KDE and maybe some other, the rest return "method not implemented" error)
                if not isFDScrUsed:
                    # add default section with the actual standard
                    iNames.extend(["org.freedesktop.ScreenSaver"])
                    iPaths.extend(["/org/freedesktop/ScreenSaver"])

            # THIS WHOLE SECTION IS WORKAROUNDS FOR MULTIPLE VARIETIES OF SCREENSAVER IMPLEMENTATIONS - END

            # go through inames
            for idx in range(len(iNames)):
                # go through all possible interfaces
                try:
                    # dbus performance measurement
                    misc.measureDBUSTimeElapsed(pStart=True)
                    # getting interface
                    self._dbusConnections[self.CL_CONN_SCR][self.CL_IF] = (
                        dbus.Interface(
                            self._userSessionBus.get_object(iNames[idx], iPaths[idx]),
                            iNames[idx],
                        )
                    )
                    # log
                    log.log(
                        cons.TK_LOG_LEVEL_INFO,
                        f'INFO: connected to screensaver service through "{iNames[idx]}"',
                    )
                    # verification (Gnome has not implemented freedesktop methods, we need to verify this actually works)
                    self._dbusConnections[self.CL_CONN_SCR][self.CL_IF].GetActive()
                    # measurement logging
                    misc.measureDBUSTimeElapsed(pStop=True, pDbusIFName=iNames[idx])
                    # first sucess is enough
                    chosenIdx = idx
                    # finish
                    break
                except Exception as dbusEx:
                    del self._dbusConnections[self.CL_CONN_SCR][self.CL_IF]
                    self._dbusConnections[self.CL_CONN_SCR][self.CL_IF] = None
                    # logging
                    log.log(
                        cons.TK_LOG_LEVEL_INFO,
                        f'WARNING: initiating dbus connection ("{__name__}.{self.initClientConnections.__name__}", {self.CL_CONN_SCR}, {iNames[idx]}), error: {dbusEx!s}',
                    )

            # connection successful
            if self._dbusConnections[self.CL_CONN_SCR][self.CL_IF] is not None:
                # log
                log.log(
                    cons.TK_LOG_LEVEL_DEBUG,
                    f"CONNECTED to DBUS {self.CL_CONN_SCR} ({iNames[chosenIdx]}) interface",
                )
                # add a connection to signal
                self._dbusConnections[self.CL_CONN_SCR][self.CL_SI] = (
                    self._userSessionBus.add_signal_receiver(
                        path=iPaths[chosenIdx],
                        handler_function=self.receiveScreenSaverActivityChange,
                        dbus_interface=iNames[chosenIdx],
                        signal_name="ActiveChanged",
                    )
                )
                # log
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    f'INFO: connected to screensaver active callback signal through "{iNames[chosenIdx]}"',
                )

        # only if screensaver is not ok
        if (
            self._dbusConnections[self.CL_CONN_TK][self.CL_IF] is None
            and self._dbusConnections[self.CL_CONN_TK][self.CL_CNT] > 0
            and not self._dbusConnections[self.CL_CONN_TK][self.CL_DEL] > 0
        ):
            try:
                # dbus performance measurement
                misc.measureDBUSTimeElapsed(pStart=True)
                # getting interface
                self._dbusConnections[self.CL_CONN_TK][self.CL_IF] = dbus.Interface(
                    self._timekprBus.get_object(
                        cons.TK_DBUS_BUS_NAME, cons.TK_DBUS_SERVER_PATH
                    ),
                    cons.TK_DBUS_USER_LIMITS_INTERFACE,
                )
                # log
                log.log(
                    cons.TK_LOG_LEVEL_DEBUG,
                    f"CONNECTED to {self.CL_CONN_TK} DBUS {self.CL_IF} interface",
                )
                # log
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    f'INFO: connected to timekpr limits service through "{cons.TK_DBUS_USER_LIMITS_INTERFACE}"',
                )
                # getting interface
                self._dbusConnections[self.CL_CONN_TK][self.CL_IFA] = dbus.Interface(
                    self._timekprBus.get_object(
                        cons.TK_DBUS_BUS_NAME, cons.TK_DBUS_SERVER_PATH
                    ),
                    cons.TK_DBUS_USER_SESSION_ATTRIBUTE_INTERFACE,
                )
                # log
                log.log(
                    cons.TK_LOG_LEVEL_DEBUG,
                    f"CONNECTED to {self.CL_CONN_TK} DBUS {self.CL_IFA} interface",
                )
                # log
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    f'INFO: connected to timekpr session attributes service through "{cons.TK_DBUS_USER_SESSION_ATTRIBUTE_INTERFACE}"',
                )
                # measurement logging
                misc.measureDBUSTimeElapsed(
                    pStop=True, pDbusIFName=cons.TK_DBUS_USER_LIMITS_INTERFACE
                )
            except Exception as dbusEx:
                # reset
                self._dbusConnections[self.CL_CONN_TK][self.CL_IF] = None
                self._dbusConnections[self.CL_CONN_TK][self.CL_IFA] = None
                # logging
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    f'WARNING: initiating dbus connection ("{__name__}.{self.initClientConnections.__name__}", {self.CL_CONN_TK}, {cons.TK_DBUS_BUS_NAME}), error: {dbusEx!s}',
                )

        # retry?
        doRetry = False
        # all variants
        for rConn in (self.CL_CONN_TK, self.CL_CONN_NOTIF, self.CL_CONN_SCR):
            # if either of this fails, we keep trying to connect
            # max retries
            if (
                self._dbusConnections[rConn][self.CL_IF] is None
                and self._dbusConnections[rConn][self.CL_CNT] > 0
            ):
                # only if delay is ended
                if not self._dbusConnections[rConn][self.CL_DEL] > 0:
                    # decrease retries
                    self._dbusConnections[rConn][self.CL_CNT] -= 1
                # continue if more retries available
                if self._dbusConnections[rConn][self.CL_CNT] > 0:
                    # retry
                    doRetry = True
                    # do not take into account delay
                    if self._dbusConnections[rConn][self.CL_DEL] > 0:
                        # connection delayed
                        log.log(
                            cons.TK_LOG_LEVEL_INFO,
                            f"INFO: dbus connection to {rConn} delayed for {int(self._dbusConnections[rConn][self.CL_DEL])} more times",
                        )
                        # decrease delay
                        self._dbusConnections[rConn][self.CL_DEL] -= 1
                    else:
                        # logging
                        log.log(
                            cons.TK_LOG_LEVEL_INFO,
                            f"ERROR: failed to connect to {rConn} dbus, trying again...",
                        )
                else:
                    # connection aborted
                    log.log(
                        cons.TK_LOG_LEVEL_INFO,
                        f"WARNING: dbus connection to {rConn} failed, some functionality will not be available",
                    )

        # retry
        if doRetry:
            # if either of this fails, we keep trying to connect
            GLib.timeout_add_seconds(cons.TK_POLLTIME, self.initClientConnections)
        # prepare notifications in case smth is not ok
        else:
            # let's inform user in case screensaver is not connected
            if (
                self._dbusConnections[self.CL_CONN_SCR][self.CL_IF] is None
                and self._timekprClientConfig.getClientShowAllNotifications()
            ):
                # prepare notification
                self.notifyUser(
                    cons.TK_MSG_CODE_FEATURE_SCR_NOT_AVAILABLE_ERROR,
                    None,
                    cons.TK_PRIO_WARNING,
                    pAdditionalMessage=self.CL_CONN_SCR,
                )

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finish initClientConnections")

        # finish
        return False

    def isTimekprConnected(self):
        """Return status of timekpr connection (nothing else, just timekpr itself)"""
        return self._dbusConnections[self.CL_CONN_TK][self.CL_IF] is not None

    def _prepareNotification(
        self, pMsgCode, pMsgType, pPriority, pTimeLeft=None, pAdditionalMessage=None
    ):
        """Prepare the message to be sent to dbus notifications"""
        log.log(cons.TK_LOG_LEVEL_DEBUG, "start prepareNotification")

        # determine icon to use
        timekprIcon = cons.TK_PRIO_CONF[cons.getNotificationPrioriy(pPriority)][
            cons.TK_ICON_NOTIF
        ]
        timekprPrio = cons.TK_PRIO_CONF[cons.getNotificationPrioriy(pPriority)][
            cons.TK_DBUS_PRIO
        ]

        # calculate hours in advance
        if pTimeLeft is not None:
            timeLeftHours = (
                pTimeLeft - cons.TK_DATETIME_START
            ).days * 24 + pTimeLeft.hour

        # determine the message to pass
        if pMsgCode == cons.TK_MSG_CODE_TIMEUNLIMITED:
            # no limit
            msgStr = msg.getTranslation("TK_MSG_NOTIFICATION_NOT_LIMITED")
        elif pMsgCode == cons.TK_MSG_CODE_TIMELEFT:
            # msg
            msgStr = " ".join(
                (
                    msg.getTranslation(
                        "TK_MSG_NOTIFICATION_TIME_LEFT_1", timeLeftHours
                    ),
                    msg.getTranslation(
                        "TK_MSG_NOTIFICATION_TIME_LEFT_2", pTimeLeft.minute
                    ),
                    msg.getTranslation(
                        "TK_MSG_NOTIFICATION_PLAYTIME_LEFT_3"
                        if pMsgType == "PlayTime"
                        else "TK_MSG_NOTIFICATION_TIME_LEFT_3",
                        pTimeLeft.second,
                    ),
                )
            )
        elif pMsgCode == cons.TK_MSG_CODE_TIMECRITICAL:
            # depending on type
            if pMsgType == cons.TK_CTRL_RES_L:
                msgCode = "TK_MSG_NOTIFICATION_TIME_IS_UP_1L"
            elif pMsgType in (cons.TK_CTRL_RES_S, cons.TK_CTRL_RES_W):
                msgCode = "TK_MSG_NOTIFICATION_TIME_IS_UP_1S"
            elif pMsgType == cons.TK_CTRL_RES_D:
                msgCode = "TK_MSG_NOTIFICATION_TIME_IS_UP_1D"
            else:
                msgCode = "TK_MSG_NOTIFICATION_TIME_IS_UP_1T"
            # msg
            msgStr = " ".join(
                (
                    msg.getTranslation(msgCode),
                    msg.getTranslation(
                        "TK_MSG_NOTIFICATION_TIME_IS_UP_2", pTimeLeft.second
                    ),
                )
            )
        elif pMsgCode == cons.TK_MSG_CODE_TIMELEFTCHANGED:
            # msg
            msgStr = msg.getTranslation("TK_MSG_NOTIFICATION_ALLOWANCE_CHANGED")
        elif pMsgCode == cons.TK_MSG_CODE_TIMECONFIGCHANGED:
            # msg
            msgStr = msg.getTranslation("TK_MSG_NOTIFICATION_CONFIGURATION_CHANGED")
        elif pMsgCode == cons.TK_MSG_CODE_REMOTE_COMMUNICATION_ERROR:
            # msg
            msgStr = msg.getTranslation("TK_MSG_NOTIFICATION_CANNOT_CONNECT") % (
                pAdditionalMessage
            )
        elif pMsgCode == cons.TK_MSG_CODE_REMOTE_INVOCATION_ERROR:
            # msg
            msgStr = msg.getTranslation("TK_MSG_NOTIFICATION_CANNOT_COMMUNICATE") % (
                pAdditionalMessage
            )
        elif pMsgCode == cons.TK_MSG_CODE_ICON_INIT_ERROR:
            # msg
            msgStr = msg.getTranslation("TK_MSG_NOTIFICATION_CANNOT_INIT_ICON") % (
                pAdditionalMessage
            )
        elif pMsgCode == cons.TK_MSG_CODE_FEATURE_SCR_NOT_AVAILABLE_ERROR:
            # msg
            msgStr = msg.getTranslation(
                "TK_MSG_NOTIFICATION_SCR_FEATURE_NOT_AVAILABLE"
            ) % (pAdditionalMessage)

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finish prepareNotification")

        # pass this back
        return timekprIcon, msgStr, timekprPrio

    def notifyUser(
        self, pMsgCode, pMsgType, pPriority, pTimeLeft=None, pAdditionalMessage=None
    ):
        """Notify the user."""
        # if we have dbus connection, let"s do so
        if self._dbusConnections[self.CL_CONN_NOTIF][self.CL_IF] is None:
            # init
            self.initClientConnections()
            # if not then return
            if not self.isTimekprConnected():
                return

        # can we notify user
        if self._dbusConnections[self.CL_CONN_NOTIF][self.CL_IF] is not None:
            # prepare notification
            timekprIcon, msgStr, timekprPrio = self._prepareNotification(
                pMsgCode, pMsgType, pPriority, pTimeLeft, pAdditionalMessage
            )

            # defaults
            hints = {"urgency": timekprPrio}

            # notification params based on criticality
            if pPriority in (cons.TK_PRIO_CRITICAL, cons.TK_PRIO_IMPORTANT):
                # timeout
                notificationTimeout = (
                    self._timekprClientConfig.getClientNotificationTimeoutCritical()
                )
                # sound
                if (
                    self._timekprClientConfig.getIsNotificationSoundSupported()
                    and self._timekprClientConfig.getClientUseNotificationSound()
                ):
                    # add sound hint
                    if cons.TK_CL_NOTIF_SND_TYPE == "sound-name":
                        hints["sound-name"] = cons.TK_CL_NOTIF_SND_NAME_IMPORTANT
                    else:
                        hints["sound-file"] = cons.TK_CL_NOTIF_SND_FILE_CRITICAL
            else:
                # timeout
                notificationTimeout = (
                    self._timekprClientConfig.getClientNotificationTimeout()
                )
                # sound
                if (
                    self._timekprClientConfig.getIsNotificationSoundSupported()
                    and self._timekprClientConfig.getClientUseNotificationSound()
                ):
                    # add sound hint
                    if cons.TK_CL_NOTIF_SND_TYPE == "sound-name":
                        hints["sound-name"] = cons.TK_CL_NOTIF_SND_NAME_WARNING
                    else:
                        hints["sound-file"] = cons.TK_CL_NOTIF_SND_FILE_WARN

            # calculate last time notification is shown (if this is too recent - replace, otherwise add new notification)
            if (
                pMsgType == "PlayTime"
                and self._lastPTNotifId != 0
                and abs((datetime.now() - self._lastPTNotifDT).total_seconds())
                >= (
                    notificationTimeout
                    if notificationTimeout > 0
                    else abs((datetime.now() - self._lastPTNotifDT).total_seconds()) + 1
                )
            ):
                self._lastPTNotifId = 0
            elif (
                pMsgType != "PlayTime"
                and self._lastNotifId != 0
                and abs((datetime.now() - self._lastNotifDT).total_seconds())
                >= (
                    notificationTimeout
                    if notificationTimeout > 0
                    else abs((datetime.now() - self._lastNotifDT).total_seconds()) + 1
                )
            ):
                self._lastNotifId = 0

            # calculate notification values
            notificationTimeout = (
                min(cons.TK_CL_NOTIF_MAX, max(0, notificationTimeout)) * 1000
            )

            # notification value of 0 means "forever"
            actions = ["OK", "OK"] if notificationTimeout == 0 else []

            log.log(
                cons.TK_LOG_LEVEL_DEBUG,
                "preshow: {}, {}, {}".format(
                    msg.getTranslation(
                        "TK_MSG_NOTIFICATION_PLAYTIME_TITLE"
                        if pMsgType == "PlayTime"
                        else "TK_MSG_NOTIFICATION_TITLE"
                    ),
                    msgStr,
                    int(notificationTimeout),
                ),
            )

            # notify through dbus
            try:
                # before
                notifId = 0
                # call dbus method
                notifId = self._dbusConnections[self.CL_CONN_NOTIF][self.CL_IF].Notify(
                    "Timekpr",
                    self._lastPTNotifId
                    if pMsgType == "PlayTime"
                    else self._lastNotifId,
                    timekprIcon,
                    msg.getTranslation(
                        "TK_MSG_NOTIFICATION_PLAYTIME_TITLE"
                        if pMsgType == "PlayTime"
                        else "TK_MSG_NOTIFICATION_TITLE"
                    ),
                    msgStr,
                    actions,
                    hints,
                    notificationTimeout,
                )
            except Exception as dbusEx:
                # do not need to reconnect when too many notifs
                if "ExcessNotificationGeneration" not in str(dbusEx):
                    # we cannot send notif through dbus
                    self._dbusConnections[self.CL_CONN_NOTIF][self.CL_IF] = None
                    # logging
                    log.log(
                        cons.TK_LOG_LEVEL_INFO,
                        f'ERROR (DBUS): "{dbusEx!s}" in "{__name__}.{self.notifyUser.__name__}"',
                    )
                else:
                    # logging
                    log.log(
                        cons.TK_LOG_LEVEL_INFO,
                        f'WARNING (DBUS): "{dbusEx!s}" in "{__name__}.{self.notifyUser.__name__}"',
                    )

            # save notification ID (only if message is not about PlayTime, otherwise it may dismiss standard time or vice versa)
            if pMsgType == "PlayTime":
                self._lastPTNotifId = notifId
                self._lastPTNotifDT = datetime.now()
            else:
                self._lastNotifId = notifId
                self._lastNotifDT = datetime.now()

            # user wants to hear things
            if (
                self._timekprClientConfig.getIsNotificationSpeechSupported()
                and self._timekprClientConfig.getClientUseSpeechNotifications()
            ):
                # say that out loud
                self._timekprSpeechManager.saySmth(msgStr)

    # --------------- admininstration / verification methods --------------- #

    def verifySessionAttributes(self, pWhat, pKey):
        """Receive the signal and process the data"""
        log.log(
            cons.TK_LOG_LEVEL_DEBUG,
            "prepare verification of attributes for server: {}, {}".format(
                pWhat, "key"
            ),
        )
        # def
        value = None

        # for screensaver status
        if pWhat == cons.TK_CTRL_SCR_N:
            # value
            value = str(
                bool(self._dbusConnections[self.CL_CONN_SCR][self.CL_IF].GetActive())
            )

        # resend stuff to server
        self.processUserSessionAttributes(pWhat, pKey, value)

    # --------------- admininstration / verification signals --------------- #

    def receiveScreenSaverActivityChange(self, pIsActive):
        """Receive the signal and process the data"""
        log.log(
            cons.TK_LOG_LEVEL_DEBUG,
            f"receive screensaver activity changes: {bool(pIsActive)!s}",
        )

        # request to server for verification
        self.processUserSessionAttributes(cons.TK_CTRL_SCR_N)

    def receiveNotificationClosed(self, pNotifId, pReason):
        """Receive the signal and process the data"""
        log.log(
            cons.TK_LOG_LEVEL_DEBUG,
            f"receive notification closed: {int(pNotifId)}, {int(pReason)}",
        )

        # check and reset which notification has changed
        if self._lastNotifId == pNotifId:
            self._lastNotifId = 0
        elif self._lastPTNotifId == pNotifId:
            self._lastPTNotifId = 0

    # --------------- request methods to timekpr --------------- #

    def requestTimeLeft(self):
        """Request time left from server"""
        # if we have dbus connection, let"s do so
        if self._dbusConnections[self.CL_CONN_TK][self.CL_IF] is None:
            # init
            self.initClientConnections()

        # if we have end-point
        if self._dbusConnections[self.CL_CONN_TK][self.CL_IF] is not None:
            log.log(cons.TK_LOG_LEVEL_INFO, "requesting timeleft")
            # notify through dbus
            try:
                # call dbus method
                result, message = self._dbusConnections[self.CL_CONN_TK][
                    self.CL_IF
                ].requestTimeLeft(self._userName)

                # check call result
                if result != 0:
                    # show message to user as well
                    self.notifyUser(
                        cons.TK_MSG_CODE_REMOTE_INVOCATION_ERROR,
                        None,
                        cons.TK_PRIO_CRITICAL,
                        pAdditionalMessage=message,
                    )
            except Exception as dbusEx:
                # we cannot send notif through dbus
                self._dbusConnections[self.CL_CONN_TK][self.CL_IF] = None
                # logging
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    f'ERROR (DBUS): "{dbusEx!s}" in "{__name__}.{self.requestTimeLeft.__name__}"',
                )

                # show message to user as well
                self.notifyUser(
                    cons.TK_MSG_CODE_REMOTE_COMMUNICATION_ERROR,
                    None,
                    cons.TK_PRIO_CRITICAL,
                    pAdditionalMessage=msg.getTranslation(
                        "TK_MSG_NOTIFICATION_CONNECTION_ERROR"
                    ),
                )

    def requestTimeLimits(self):
        """Request time limits from server"""
        # if we have dbus connection, let"s do so
        if self._dbusConnections[self.CL_CONN_TK][self.CL_IF] is None:
            # init
            self.initClientConnections()

        # if we have end-point
        if self._dbusConnections[self.CL_CONN_TK][self.CL_IF] is not None:
            log.log(cons.TK_LOG_LEVEL_INFO, "requesting timelimits")
            # notify through dbus
            try:
                # call dbus method
                result, message = self._dbusConnections[self.CL_CONN_TK][
                    self.CL_IF
                ].requestTimeLimits(self._userName)

                # check call result
                if result != 0:
                    # show message to user as well
                    self.notifyUser(
                        cons.TK_MSG_CODE_REMOTE_INVOCATION_ERROR,
                        None,
                        cons.TK_PRIO_CRITICAL,
                        pAdditionalMessage=message,
                    )
            except Exception as dbusEx:
                # we cannot send notif through dbus
                self._dbusConnections[self.CL_CONN_TK][self.CL_IF] = None
                # logging
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    f'ERROR (DBUS): "{dbusEx!s}" in "{__name__}.{self.requestTimeLimits.__name__}"',
                )

                # show message to user as well
                self.notifyUser(
                    cons.TK_MSG_CODE_REMOTE_COMMUNICATION_ERROR,
                    None,
                    cons.TK_PRIO_CRITICAL,
                    pAdditionalMessage=msg.getTranslation(
                        "TK_MSG_NOTIFICATION_CONNECTION_ERROR"
                    ),
                )

    def processUserSessionAttributes(self, pWhat, pKey=None, pValue=None):
        """Process user session attributes from server"""
        # if we have dbus connection, let"s do so
        if self._dbusConnections[self.CL_CONN_TK][self.CL_IFA] is None:
            # init
            self.initClientConnections()

        # if we have end-point
        if self._dbusConnections[self.CL_CONN_TK][self.CL_IFA] is not None:
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                "%s session attributes"
                % ("requesting" if pKey is None else "verifying"),
            )
            # notify through dbus
            try:
                # call dbus method
                result, message = self._dbusConnections[self.CL_CONN_TK][
                    self.CL_IFA
                ].processUserSessionAttributes(
                    self._userName,
                    dbus.String(pWhat if pWhat is not None else ""),
                    dbus.String(pKey if pKey is not None else ""),
                    dbus.String(pValue if pValue is not None else ""),
                )

                # check call result
                if result != 0:
                    # show message to user as well
                    self.notifyUser(
                        cons.TK_MSG_CODE_REMOTE_INVOCATION_ERROR,
                        None,
                        cons.TK_PRIO_CRITICAL,
                        None,
                        pAdditionalMessage=message,
                    )
            except Exception as dbusEx:
                # we cannot send notif through dbus
                self._dbusConnections[self.CL_CONN_TK][self.CL_IF] = None
                self._dbusConnections[self.CL_CONN_TK][self.CL_IFA] = None
                # logging
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    f'ERROR (DBUS): "{dbusEx!s}" in "{__name__}.{self.processUserSessionAttributes.__name__}"',
                )

                # show message to user as well
                self.notifyUser(
                    cons.TK_MSG_CODE_REMOTE_COMMUNICATION_ERROR,
                    None,
                    cons.TK_PRIO_CRITICAL,
                    pAdditionalMessage=msg.getTranslation(
                        "TK_MSG_NOTIFICATION_CONNECTION_ERROR"
                    ),
                )
