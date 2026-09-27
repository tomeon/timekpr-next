"""
Created on Aug 28, 2018

@author: mjasnik
"""

# import section
import os
import threading
import time
import traceback
from datetime import datetime, timedelta

import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

# timekpr imports
from timekpr.common.constants import constants as cons
from timekpr.common.constants import messages as msg
from timekpr.common.log import log
from timekpr.common.utils import misc
from timekpr.common.utils.config import timekprConfig
from timekpr.server.config import userhelper
from timekpr.server.config.configprocessor import (
    timekprConfigurationProcessor,
    timekprUserConfigurationProcessor,
)
from timekpr.server.config.policy import isGroupTarget, timekprPolicyStore
from timekpr.server.config.userhelper import timekprUserStore
from timekpr.server.interface.dbus.logind import manager as l1_manager
from timekpr.server.interface.dbus.polkit import (
    timekprAuthorizedMethod,
    timekprPolkitAuthority,
)
from timekpr.server.user.userdata import timekprUser

# default dbus
DBusGMainLoop(set_as_default=True)


class timekprDaemon(dbus.service.Object):
    """Main daemon class"""

    # --------------- initialization / control methods --------------- #
    def __init__(self):
        """Initialize daemon variables"""
        log.log(cons.TK_LOG_LEVEL_INFO, "start init dbus daemon")

        # get our bus
        self._timekprBus = (
            dbus.SessionBus()
            if (cons.TK_DEV_ACTIVE and cons.TK_DEV_BUS == "ses")
            else dbus.SystemBus()
        )
        # get our bus name (where clients will find us)
        self._timekprBusName = dbus.service.BusName(
            cons.TK_DBUS_BUS_NAME, bus=self._timekprBus, replace_existing=True
        )
        # init DBUS
        super().__init__(self._timekprBusName, cons.TK_DBUS_SERVER_PATH)
        # this decides who may call what (the admin interfaces go through polkit)
        self._timekprPolkitAuthority = timekprPolkitAuthority(self._timekprBus)

        log.log(cons.TK_LOG_LEVEL_INFO, "finish init dbus daemon")

    def initTimekpr(self):
        """Init all the required attributes"""
        log.log(cons.TK_LOG_LEVEL_DEBUG, "start init daemon data")

        # ## variables ##
        # init main loop
        self._timekprMainLoop = GLib.MainLoop()
        # init termination trigger
        self._finishExecution = False
        # this will define login manager
        self._timekprLoginManagerName = "L1"
        # this will define login manager
        self._timekprLoginManager = None
        # this will define main timekpr configuration loader
        self._timekprConfig = None
        # this will hold all timekpr users (collection of user class)
        self._timekprUserList = {}
        # this will hold collection of users to be terminated
        self._timekprUserTerminationList = {}
        # this will hold collection of users who have restrictions to use computer
        self._timekprUserRestrictionList = {}

        # ## initialization ##
        # configuration init
        self._timekprConfig = timekprConfig()
        self._timekprConfig.loadMainConfiguration()
        # log
        self._timekprConfig.logMainConfiguration()

        # init logging
        log.setLogging(
            self._timekprConfig.getTimekprLogLevel(),
            self._timekprConfig.getTimekprLogfileDir(),
            cons.TK_LOG_OWNER_SRV,
            "",
        )

        # the policies
        self._timekprPolicyStore = timekprPolicyStore(
            self._timekprConfig.getTimekprConfigDir()
        )
        # the policy files of earlier versions go into the policy database
        try:
            self._timekprPolicyStore.importPolicyFiles()
        except Exception:
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"ERROR: the policy files could not be imported into the policy database:\n{traceback.format_exc()}",
            )
        # user policies left over from versions that created one per user
        self._timekprPolicyStore.warnAboutDefaultPolicies()

        # in case we are dealing with logind
        if self._timekprLoginManagerName == "L1":
            self._timekprLoginManager = l1_manager.timekprUserLoginManager()
        # in case we are dealing with consolekit (WHICH IS NOT IMPLEMENTED YET and might NOT be AT ALL)
        elif self._timekprLoginManagerName == "CK":
            self._timekprLoginManager = None

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finish init daemon data")

    def finishTimekpr(self, signal=None, frame=None):
        """Exit timekpr gracefully"""
        # show all threads that we are exiting
        self._finishExecution = True
        # exit main loop
        self._timekprMainLoop.quit()
        log.log(cons.TK_LOG_LEVEL_INFO, "main loop shut down")

    def executeTimekprMain(self):
        """Start up main loop"""
        log.log(cons.TK_LOG_LEVEL_INFO, "start up main loop thread")

        # wrap in handlers, so we can finish gracefully
        try:
            self._timekprMainLoop.run()
        except KeyboardInterrupt:
            log.log(cons.TK_LOG_LEVEL_INFO, "asking everything to shut down")
            # set up finishing flag
            self.finishTimekpr()

        # finish logging
        log.flushLogFile()

    def executeTimekprWorker(self):
        """Execute all the logic of timekpr"""
        log.log(cons.TK_LOG_LEVEL_INFO, "start up worker thread")
        # def
        execLen = timedelta(0, 0, 0)
        execCnt = 0
        # we execute tasks until not asked to stop
        while not self._finishExecution:
            # perf
            dtsm = time.time()
            dts = datetime.now()
            log.log(cons.TK_LOG_LEVEL_INFO, "--- start working on users ---")

            # do the actual work
            try:
                self.checkUsers()
            except Exception:
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    '---=== ERROR in "executeTimekprWorker" working on users ===---',
                )
                log.log(cons.TK_LOG_LEVEL_INFO, traceback.format_exc())
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    '---=== ERROR in "executeTimekprWorker" working on users ===---',
                )

            # periodically flush the file
            log.autoFlushLogFile()

            # perf
            lavg = os.getloadavg()
            perf = datetime.now() - dts
            execCnt += 1
            execLen += perf

            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"--- end working on users (ela: {perf!s}) ---",
            )
            log.log(
                cons.TK_LOG_LEVEL_DEBUG,
                f"--- perf: avg ela: {execLen / execCnt!s}, loadavg: {lavg[0]}, {lavg[1]}, {lavg[2]} ---",
            )
            # take a polling pause (try to do that exactly every 3 secs)
            time.sleep(
                self._timekprConfig.getTimekprPollTime()
                - min(time.time() - dtsm, self._timekprConfig.getTimekprPollTime() / 2)
            )

        log.log(cons.TK_LOG_LEVEL_INFO, "worker shut down")
        # finish logging
        log.flushLogFile()

    def startTimekprDaemon(self):
        """Enable threading for all the tasks"""
        log.log(cons.TK_LOG_LEVEL_INFO, "start daemons")

        # set up main loop
        self._timekprMainLoopTh = threading.Thread(target=self.executeTimekprMain)
        # set up worker
        self._timekprWorkTh = threading.Thread(target=self.executeTimekprWorker)

        # start both
        self._timekprMainLoopTh.start()
        self._timekprWorkTh.start()

        log.log(cons.TK_LOG_LEVEL_INFO, "finish daemons, timekpr started")

    # --------------- worker methods --------------- #

    def checkUsers(self):
        """Entry point for user management logic"""
        log.log(cons.TK_LOG_LEVEL_EXTRA_DEBUG, "start checkUsers")

        # get user list
        wasConnectionLost, userList = self._timekprLoginManager.getUserList()
        # if we had a disaster, remove all users because connection to DBUS was lost
        if wasConnectionLost:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                "IMPORTANT WARNING: due to lost DBUS connection, all users are de-initialized (including from DBUS) and re-initalized from saved state",
            )
            # remove them from dbus
            for rUser in self._timekprUserList:
                # remove from DBUS
                self._timekprUserList[rUser].deInitUser()
            # delete all users
            self._timekprUserList.clear()
            # delete termination list as well
            self._timekprUserRestrictionList.clear()

        # add new users to track
        for rUserName, userDict in userList.items():
            # login manager is system user, we do these checks only for system users
            if not userhelper.isUserValid(
                userDict[cons.TK_CTRL_UID], userDict[cons.TK_CTRL_UNAME]
            ):
                # sys user
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    f'NOTE: system or mismatched user "{rUserName}" explicitly excluded',
                )
                # try to get login manager VT (if not already found)
                self._timekprLoginManager.determineLoginManagerVT(
                    rUserName, userDict[cons.TK_CTRL_UPATH]
                )
            # if username is in exclusion list, additionally verify that username is not a sysuser / login manager (this is somewhat obsolete now)
            elif (
                rUserName in self._timekprConfig.getTimekprUsersExcl()
                and rUserName not in userhelper.getTimekprLoginManagers()
            ):
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    f'NOTE: user "{rUserName}" explicitly excluded',
                )
            # if not in, we add it
            elif rUserName not in self._timekprUserList:
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    f'NOTE: we have a new user "{rUserName}"',
                )
                # add user
                self._timekprUserList[rUserName] = timekprUser(
                    self._timekprBusName,
                    userDict[cons.TK_CTRL_UID],
                    userDict[cons.TK_CTRL_UNAME],
                    userDict[cons.TK_CTRL_UPATH],
                    self._timekprConfig,
                )

                # adjust config
                self._timekprUserList[rUserName].adjustLimitsFromConfig()
                # adjust time spent
                self._timekprUserList[rUserName].adjustTimeSpentFromControl()

        # session list to remove
        removableUsers = [
            rUserName
            for rUserName in self._timekprUserList
            if rUserName not in userList
        ]

        # get rid of users which left
        for rUserName in removableUsers:
            log.log(cons.TK_LOG_LEVEL_INFO, f'NOTE: user "{rUserName}" has gone')
            # save everything for the user
            self._timekprUserList[rUserName].saveSpent()
            self._timekprUserList[rUserName].deInitUser()
            # delete users that left
            self._timekprUserList.pop(rUserName)
            # remove if exists
            if rUserName in self._timekprUserRestrictionList:
                # delete from killing list as well
                self._timekprUserRestrictionList.pop(rUserName)

        # go through all users
        for rUserName in self._timekprUserList:
            # init variables for user
            self._timekprUserList[rUserName].refreshTimekprRuntimeVariables()

            # adjust time spent
            userActiveEffective, userActiveActual, userScreenLocked = (
                self._timekprUserList[rUserName].adjustTimeSpentActual(
                    self._timekprConfig
                )
            )
            # recalculate time left
            self._timekprUserList[rUserName].recalculateTimeLeft()
            # process actual user session variable validation
            self._timekprUserList[rUserName].revalidateUserSessionAttributes()

            # get stats for user
            timeLeftArray = self._timekprUserList[rUserName].getTimeLeft()
            timeLeftToday = timeLeftArray[0]
            timeLeftInARow = timeLeftArray[1]
            timeHourUnaccounted = timeLeftArray[6]

            # logging
            log.log(
                cons.TK_LOG_LEVEL_DEBUG,
                f'user "{rUserName}", active: {userActiveActual!s}/{userActiveEffective!s}/{userScreenLocked!s} (act/eff/lck), huacc: {timeHourUnaccounted!s}, tleft: {int(timeLeftInARow)}',
            )

            # process actions if user is in the restrictions list
            if rUserName in self._timekprUserRestrictionList:
                # (internal idle killing switch) + user is not active + there is a time available today (opposing to in a row)
                if (
                    not userActiveActual
                    and timeLeftToday > self._timekprConfig.getTimekprTerminationTime()
                ) or timeHourUnaccounted:
                    log.log(
                        cons.TK_LOG_LEVEL_INFO,
                        f'SAVING user "{rUserName}" from ending his sessions',
                    )
                    # remove from death list
                    self._timekprUserRestrictionList.pop(rUserName)
                # if restricted time has passed, we need to lift the restriction
                elif (
                    timeLeftInARow > self._timekprConfig.getTimekprTerminationTime()
                    or timeHourUnaccounted
                ):
                    log.log(
                        cons.TK_LOG_LEVEL_INFO,
                        f'RELEASING terminate from user "{rUserName}"',
                    )
                    # remove from restriction list
                    self._timekprUserRestrictionList.pop(rUserName)
                # update restriction stats
                elif userActiveActual:
                    # only if user is active
                    self._timekprUserRestrictionList[rUserName][cons.TK_CTRL_RTDEL] = (
                        max(
                            self._timekprUserRestrictionList[rUserName][
                                cons.TK_CTRL_RTDEL
                            ]
                            - 1,
                            0,
                        )
                    )

            # ## FILL IN USER RESTRICTIONS ##

            # if user has very few time left, we need to enforce limits: Terminate sessions
            if (
                timeLeftInARow <= self._timekprConfig.getTimekprTerminationTime()
                and not timeHourUnaccounted
                and rUserName not in self._timekprUserRestrictionList
                and userActiveActual
            ):
                log.log(
                    cons.TK_LOG_LEVEL_DEBUG,
                    f'INFO: user "{rUserName}" has got restrictions...',
                )
                # add user to restrictions list
                self._timekprUserRestrictionList[rUserName] = {
                    cons.TK_CTRL_UPATH: self._timekprUserList[
                        rUserName
                    ].getUserPathOnBus(),  # user path on dbus
                    cons.TK_CTRL_FCNTD: max(
                        timeLeftInARow, self._timekprConfig.getTimekprTerminationTime()
                    ),  # final countdown
                    cons.TK_CTRL_RTDEL: 0,  # retry delay before next attempt to enforce restrictions
                }
                # in case this is first restriction we need to initiate restriction process
                if len(self._timekprUserRestrictionList) == 1:
                    # process users
                    GLib.timeout_add_seconds(1, self._restrictUsers)

        log.log(cons.TK_LOG_LEVEL_EXTRA_DEBUG, "finish checkUsers")

    def _restrictUsers(self):
        """Terminate user sessions"""
        log.log(cons.TK_LOG_LEVEL_EXTRA_DEBUG, "start user killer")

        # final warn
        def _processFinalWarning(pUserName, pFinalNotificationType, pSecondsLeft):
            # process final warning with error catch (so it won't interfere with ending the sessions)
            try:
                self._timekprUserList[pUserName].processFinalWarning(
                    pFinalNotificationType, pSecondsLeft
                )
            except Exception:
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    f"ERROR sending notification while terminating users:\n{traceback.format_exc()}",
                )

        # loop through users to be killed
        for rUserName in self._timekprUserRestrictionList:
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f'RESTRICTIONS, usr: "{rUserName}", cntd: {int(self._timekprUserRestrictionList[rUserName][cons.TK_CTRL_FCNTD])}, del: {int(self._timekprUserRestrictionList[rUserName][cons.TK_CTRL_RTDEL])}',
            )
            # we are going to TERMINATE user sessions
            # log that we are going to terminate user sessions
            if self._timekprUserRestrictionList[rUserName][cons.TK_CTRL_RTDEL] <= 0:
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    f"TERMINATE approaching in {self._timekprUserRestrictionList[rUserName][cons.TK_CTRL_FCNTD]!s} secs",
                )
                # send messages only when certain time is left
                if (
                    self._timekprUserRestrictionList[rUserName][cons.TK_CTRL_FCNTD]
                    <= self._timekprConfig.getTimekprFinalWarningTime()
                ):
                    # final warning
                    _processFinalWarning(
                        rUserName,
                        cons.TK_CTRL_RES_T,
                        self._timekprUserRestrictionList[rUserName][cons.TK_CTRL_FCNTD],
                    )
                # time to die
                if self._timekprUserRestrictionList[rUserName][cons.TK_CTRL_FCNTD] <= 0:
                    # set restriction for repetitive termination (ticks before next attempt)
                    self._timekprUserRestrictionList[rUserName][cons.TK_CTRL_RTDEL] = 5
                    # save user before termination
                    self._timekprUserList[rUserName].saveSpent()
                    # terminate user sessions
                    try:
                        # terminate
                        self._timekprLoginManager.terminateUserSessions(
                            rUserName,
                            self._timekprUserRestrictionList[rUserName][
                                cons.TK_CTRL_UPATH
                            ],
                            self._timekprConfig,
                        )
                    except Exception:
                        log.log(
                            cons.TK_LOG_LEVEL_INFO,
                            f"ERROR terminating sessions: {traceback.format_exc()}",
                        )

            # decrease time for restrictions
            self._timekprUserRestrictionList[rUserName][cons.TK_CTRL_FCNTD] = max(
                self._timekprUserRestrictionList[rUserName][cons.TK_CTRL_FCNTD] - 1, 0
            )

        log.log(
            cons.TK_LOG_LEVEL_INFO,
            f"RESTRICTIONS, completed with: {len(self._timekprUserRestrictionList) > 0!s}",
        )

        log.log(cons.TK_LOG_LEVEL_EXTRA_DEBUG, "finish user killer")

        # return whether to keep trying to enforce restrictions
        return len(self._timekprUserRestrictionList) > 0

    # --------------- helper methods --------------- #

    def _getUserActualTimeInformation(self, pTimekprUser, pUserConfigurationStore):
        """Helper to provide actual (in memory information)"""
        # values from live session
        if pTimekprUser is not None:
            # get lefts
            timeLeftArray = pTimekprUser.getTimeLeft()
            # assign time lefts
            timeLeftToday = timeLeftArray[0]
            timeLeftInARow = timeLeftArray[1]
            timeSpentThisSession = timeLeftArray[2]
            timeInactiveThisSession = timeLeftArray[3]
            timeSpentBalance = timeLeftArray[4]
            timeSpentDay = timeLeftArray[5]

            # time spent session
            pUserConfigurationStore["ACTUAL_TIME_SPENT_SESSION"] = int(
                timeSpentThisSession
            )
            # time inactive this session
            pUserConfigurationStore["ACTUAL_TIME_INACTIVE_SESSION"] = int(
                timeInactiveThisSession
            )
            # time spent
            pUserConfigurationStore["ACTUAL_TIME_SPENT_BALANCE"] = int(timeSpentBalance)
            # time spent
            pUserConfigurationStore["ACTUAL_TIME_SPENT_DAY"] = int(timeSpentDay)
            # time left today
            pUserConfigurationStore["ACTUAL_TIME_LEFT_DAY"] = int(timeLeftToday)
            # time left in a row
            pUserConfigurationStore["ACTUAL_TIME_LEFT_CONTINUOUS"] = int(timeLeftInARow)

    # ## --------------- DBUS / communication methods --------------- ## #
    # --------------- simple user time limits methods accessible by the user in question (and root) --------------- #

    @dbus.service.method(
        cons.TK_DBUS_USER_LIMITS_INTERFACE,
        in_signature="s",
        out_signature="is",
        sender_keyword="pSender",
    )
    def requestTimeLimits(self, pUserName, pSender=None):
        """Request to send config to client (returns error in case no user and the like)"""
        # only the user themselves (or root) may ask
        self._timekprPolkitAuthority.checkSenderIsUser(
            pUserName, pSender, self._timekprUserList
        )
        # result
        result = -1
        message = msg.getTranslation("TK_MSG_CONFIG_LOADER_USER_NOTFOUND") % (pUserName)

        # check if we have this user
        if pUserName in self._timekprUserList:
            # pass this to actual method
            self._timekprUserList[pUserName].getTimeLimits()

            # result
            result = 0
            message = ""

        # result
        return result, message

    @dbus.service.method(
        cons.TK_DBUS_USER_LIMITS_INTERFACE,
        in_signature="s",
        out_signature="is",
        sender_keyword="pSender",
    )
    def requestTimeLeft(self, pUserName, pSender=None):
        """Request to send current state of time & limits for user (returns error in case no user and the like)"""
        # only the user themselves (or root) may ask
        self._timekprPolkitAuthority.checkSenderIsUser(
            pUserName, pSender, self._timekprUserList
        )
        # result
        result = -1
        message = msg.getTranslation("TK_MSG_CONFIG_LOADER_USER_NOTFOUND") % (pUserName)

        # check if we have this user
        if pUserName in self._timekprUserList:
            # pass this to actual method
            self._timekprUserList[pUserName].getTimeLeft(True)

            # result
            result = 0
            message = ""

        # result
        return result, message

    # --------------- simple user session attributes accessible by the user in question (and root) --------------- #

    @dbus.service.method(
        cons.TK_DBUS_USER_SESSION_ATTRIBUTE_INTERFACE,
        in_signature="ssss",
        out_signature="is",
        sender_keyword="pSender",
    )
    def processUserSessionAttributes(
        self, pUserName, pWhat, pKey, pValue, pSender=None
    ):
        """Request to verify or set user session attributes (returns error in case no user and the like)"""
        # only the user themselves (or root) may set these
        self._timekprPolkitAuthority.checkSenderIsUser(
            pUserName, pSender, self._timekprUserList
        )
        # result
        result = -1
        message = msg.getTranslation("TK_MSG_CONFIG_LOADER_USER_NOTFOUND") % (pUserName)

        # check if we have this user
        if pUserName in self._timekprUserList:
            # pass this to actual method
            self._timekprUserList[pUserName].processUserSessionAttributes(
                pWhat, pKey, pValue
            )

            # result
            result = 0
            message = ""

        # result
        return result, message

    # --------------- user information get methods accessible by privileged users (root and all in timekpr group) --------------- #

    @timekprAuthorizedMethod(
        cons.TK_DBUS_USER_ADMIN_INTERFACE, "", "isaas", cons.TK_POLKIT_ACTION_READ
    )
    def getUserList(self):
        """Get user list and their time left"""
        """Sets allowed days for the user
            server expects only the days that are allowed, sorted in ascending order"""
        # result
        result = 0
        message = ""
        userList = []

        try:
            # init store
            timekprUStore = timekprUserStore()
            # check if we have this user
            userList = timekprUStore.getSavedUserList(
                self._timekprConfig.getTimekprConfigDir(),
                list(self._timekprUserList),
            )
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_USERLIST_UNEXPECTED_ERROR"
            )

        # result
        return result, message, userList

    @timekprAuthorizedMethod(
        cons.TK_DBUS_USER_ADMIN_INTERFACE,
        "ss",
        "isa{sv}",
        cons.TK_POLKIT_ACTION_READ,
        pUserNameArg="pUserName",
    )
    def getUserInformation(self, pUserName, pInfoLvl):
        """Get user configuration (saved)"""
        """  this retrieves stored configuration and some realtime inforamation for the user"""
        # initialize username storage
        userConfigurationStore = {}
        result = 0
        message = ""

        try:
            # only saved and full
            if pInfoLvl in (cons.TK_CL_INF_FULL, cons.TK_CL_INF_SAVED):
                # check the user and it's configuration
                userConfigProcessor = timekprUserConfigurationProcessor(
                    pUserName, self._timekprConfig
                )
                # load config
                result, message, userConfigurationStore = (
                    userConfigProcessor.getSavedUserInformation(
                        pInfoLvl, pUserName in self._timekprUserList
                    )
                )

            # additionally, if realtime needed
            if (
                pInfoLvl in (cons.TK_CL_INF_FULL, cons.TK_CL_INF_RT)
                and pUserName in self._timekprUserList
            ):
                # get in-memory settings
                self._getUserActualTimeInformation(
                    self._timekprUserList[pUserName], userConfigurationStore
                )
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation("TK_MSG_CONFIG_LOADER_USER_UNEXPECTED_ERROR")

        # result
        return result, message, userConfigurationStore

    # --------------- policy helpers --------------- #

    def _refreshPolicies(self, pTarget):
        """After a policy changed, make the logged-in users it may concern
        resolve theirs again: the one user for a user target, everyone for a
        group target (membership decides whom it touches)"""
        if isGroupTarget(pTarget):
            # (a copy: the worker thread adds and removes users)
            for rUser in list(self._timekprUserList.values()):
                rUser.refreshPolicyIfChanged(pSilent=False)
        elif pTarget in self._timekprUserList:
            self._timekprUserList[pTarget].adjustLimitsFromConfig(False)

    # --------------- user admin methods accessible by privileged users (root and all in timekpr group) --------------- #

    @timekprAuthorizedMethod(
        cons.TK_DBUS_USER_ADMIN_INTERFACE, "", "isaas", cons.TK_POLKIT_ACTION_READ
    )
    def getGroupList(self):
        """Get the groups with a policy: [group, overrides, known members]"""
        # result
        result = 0
        message = ""
        groupList = []

        try:
            # init store
            timekprUStore = timekprUserStore()
            # the groups
            groupList = timekprUStore.getSavedGroupList(
                self._timekprConfig.getTimekprConfigDir()
            )
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_USERLIST_UNEXPECTED_ERROR"
            )

        # result
        return result, message, groupList

    @timekprAuthorizedMethod(
        cons.TK_DBUS_USER_ADMIN_INTERFACE,
        "sas",
        "is",
        cons.TK_POLKIT_ACTION_USER_CONFIGURE,
        pUserNameArg="pUserName",
    )
    def setOverrides(self, pUserName, pOverrides):
        """Set the groups a group's policy takes precedence over (for users in both)"""
        try:
            # check the group and its configuration
            userConfigProcessor = timekprUserConfigurationProcessor(
                pUserName, self._timekprConfig
            )

            # load config
            result, message = userConfigProcessor.checkAndSetOverrides(pOverrides)

            # inform the users concerned immediately
            self._refreshPolicies(pUserName)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_USER_ADMIN_INTERFACE,
        "ss",
        "is",
        cons.TK_POLKIT_ACTION_USER_CONFIGURE,
        pUserNameArg="pUserName",
    )
    def unsetSetting(self, pUserName, pSetting):
        """Take a setting out of the policy of a user or a group (the group
        policies or the defaults decide it again)"""
        try:
            # check the target and its configuration
            userConfigProcessor = timekprUserConfigurationProcessor(
                pUserName, self._timekprConfig
            )

            # unset
            result, message = userConfigProcessor.checkAndUnsetSetting(pSetting)

            # inform the users concerned immediately
            self._refreshPolicies(pUserName)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_USER_ADMIN_INTERFACE,
        "sasa{sv}",
        "iss",
        cons.TK_POLKIT_ACTION_USER_CONFIGURE,
        pUserNameArg="pUserName",
    )
    def applyPolicyChanges(self, pUserName, pUnset, pChanges):
        """Change several settings of the policy of a user or a group at
        once, all or nothing: the settings in pUnset are taken out, the
        ones in pChanges set (both by the names of USER_CONFIG_SETTINGS,
        see timekprUserConfigurationProcessor.applyPolicyChanges); the
        third value names the setting that was refused"""
        refused = ""
        try:
            # check the target and its configuration
            userConfigProcessor = timekprUserConfigurationProcessor(
                pUserName, self._timekprConfig
            )

            # apply
            result, message, refused = userConfigProcessor.applyPolicyChanges(
                pUnset, pChanges
            )

            # inform the users concerned immediately
            self._refreshPolicies(pUserName)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message, refused

    @timekprAuthorizedMethod(
        cons.TK_DBUS_USER_ADMIN_INTERFACE,
        "s",
        "is",
        cons.TK_POLKIT_ACTION_USER_CONFIGURE,
        pUserNameArg="pUserName",
    )
    def deletePolicy(self, pUserName):
        """Delete the policy of a user (their group policies apply again) or of a group"""
        try:
            # check the target and its configuration
            userConfigProcessor = timekprUserConfigurationProcessor(
                pUserName, self._timekprConfig
            )

            # delete
            result, message = userConfigProcessor.deletePolicy()

            # inform the users concerned immediately (a deleted group
            # policy may change anyone's effective policy)
            self._refreshPolicies(pUserName)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_USER_ADMIN_INTERFACE,
        "b",
        "isas",
        cons.TK_POLKIT_ACTION_USER_CONFIGURE,
    )
    def migratePolicies(self, pDryRun):
        """Delete (or with pDryRun only list) the user policies that restrict nothing,
        left over from versions that created a policy for every user"""
        # result
        users = []
        try:
            # the store
            userConfigProcessor = timekprUserConfigurationProcessor(
                "", self._timekprConfig
            )

            # migrate
            result, message, users = userConfigProcessor.migratePolicies(bool(pDryRun))

            # inform the users concerned immediately
            if not pDryRun:
                for rUser in users:
                    self._refreshPolicies(rUser)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message, users

    # --------------- user admin set methods accessible by privileged users (root and all in timekpr group) --------------- #

    @timekprAuthorizedMethod(
        cons.TK_DBUS_USER_ADMIN_INTERFACE,
        "sas",
        "is",
        cons.TK_POLKIT_ACTION_USER_CONFIGURE,
        pUserNameArg="pUserName",
    )
    def setAllowedDays(self, pUserName, pDayList):
        """Set up allowed days for the user"""
        """Sets allowed days for the user
            server expects only the days that are allowed, sorted in ascending order"""
        try:
            # check the user and it's configuration
            userConfigProcessor = timekprUserConfigurationProcessor(
                pUserName, self._timekprConfig
            )

            # load config
            result, message = userConfigProcessor.checkAndSetAllowedDays(pDayList)

            # inform the users concerned immediately
            self._refreshPolicies(pUserName)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_USER_ADMIN_INTERFACE,
        "ssa{sa{si}}",
        "is",
        cons.TK_POLKIT_ACTION_USER_CONFIGURE,
        pUserNameArg="pUserName",
    )
    def setAllowedHours(self, pUserName, pDayNumber, pHourList):
        """Set up allowed hours for the user"""
        """This sets allowed hours for user for particular day
            server expects only the hours that are needed, hours must be sorted in ascending order
            please note that this is using 24h format, no AM/PM nonsense expected
            minutes can be specified in brackets after hour, like: 16[00-45], which means until 16:45"""
        try:
            # check the user and it's configuration
            userConfigProcessor = timekprUserConfigurationProcessor(
                pUserName, self._timekprConfig
            )

            # load config
            result, message = userConfigProcessor.checkAndSetAllowedHours(
                pDayNumber, pHourList
            )

            # inform the users concerned immediately
            self._refreshPolicies(pUserName)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_USER_ADMIN_INTERFACE,
        "sai",
        "is",
        cons.TK_POLKIT_ACTION_USER_CONFIGURE,
        pUserNameArg="pUserName",
    )
    def setTimeLimitForDays(self, pUserName, pDayLimits):
        """Set up new timelimits for each day for the user"""
        """This sets allowable time to user
            server always expects 7 limits, for each day of the week, in the list"""
        try:
            # check the user and it's configuration
            userConfigProcessor = timekprUserConfigurationProcessor(
                pUserName, self._timekprConfig
            )

            # load config
            result, message = userConfigProcessor.checkAndSetTimeLimitForDays(
                pDayLimits
            )

            # inform the users concerned immediately
            self._refreshPolicies(pUserName)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_USER_ADMIN_INTERFACE,
        "sb",
        "is",
        cons.TK_POLKIT_ACTION_USER_CONFIGURE,
        pUserNameArg="pUserName",
    )
    def setTrackInactive(self, pUserName, pTrackInactive):
        """Set track inactive sessions for the user"""
        """This sets whether inactive user sessions are tracked
            true - logged in user is always tracked (even if switched to console or locked or ...)
            false - user time is not tracked if he locks the session, session is switched to another user, etc."""
        try:
            # check the user and it's configuration
            userConfigProcessor = timekprUserConfigurationProcessor(
                pUserName, self._timekprConfig
            )

            # load config
            result, message = userConfigProcessor.checkAndSetTrackInactive(
                bool(pTrackInactive)
            )

            # inform the users concerned immediately
            self._refreshPolicies(pUserName)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_USER_ADMIN_INTERFACE,
        "sb",
        "is",
        cons.TK_POLKIT_ACTION_USER_CONFIGURE,
        pUserNameArg="pUserName",
    )
    def setHideTrayIcon(self, pUserName, pHideTrayIcon):
        """Set hide tray icon for the user"""
        """This sets whether icon will be hidden from user
            true - icon and notifications are NOT shown to user
            false - icon and notifications are shown to user"""
        try:
            # check the user and it's configuration
            userConfigProcessor = timekprUserConfigurationProcessor(
                pUserName, self._timekprConfig
            )

            # load config
            result, message = userConfigProcessor.checkAndSetHideTrayIcon(
                bool(pHideTrayIcon)
            )

            # inform the users concerned immediately
            self._refreshPolicies(pUserName)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_USER_ADMIN_INTERFACE,
        "si",
        "is",
        cons.TK_POLKIT_ACTION_USER_CONFIGURE,
        pUserNameArg="pUserName",
    )
    def setTimeLimitForWeek(self, pUserName, pTimeLimitWeek):
        """Set up new timelimit for week for the user"""
        try:
            # check the user and it's configuration
            userConfigProcessor = timekprUserConfigurationProcessor(
                pUserName, self._timekprConfig
            )

            # load config
            result, message = userConfigProcessor.checkAndSetTimeLimitForWeek(
                pTimeLimitWeek
            )

            # inform the users concerned immediately
            self._refreshPolicies(pUserName)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_USER_ADMIN_INTERFACE,
        "si",
        "is",
        cons.TK_POLKIT_ACTION_USER_CONFIGURE,
        pUserNameArg="pUserName",
    )
    def setTimeLimitForMonth(self, pUserName, pTimeLimitMonth):
        """Set up new timelimit for month for the user"""
        try:
            # check the user and it's configuration
            userConfigProcessor = timekprUserConfigurationProcessor(
                pUserName, self._timekprConfig
            )

            # load config
            result, message = userConfigProcessor.checkAndSetTimeLimitForMonth(
                pTimeLimitMonth
            )

            # inform the users concerned immediately
            self._refreshPolicies(pUserName)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_USER_ADMIN_INTERFACE,
        "ssi",
        "is",
        cons.TK_POLKIT_ACTION_USER_TIME_LEFT,
        pUserNameArg="pUserName",
    )
    def setTimeLeft(self, pUserName, pOperation, pTimeLeft):
        """Set time left for today for the user"""
        """Sets time limits for user for this moment:
            if pOperation is "+" - more time left is addeed
            if pOperation is "-" time is subtracted
            if pOperation is "=" or empty, the time is set as it is"""
        try:
            # check the user and it's configuration
            userControlProcessor = timekprUserConfigurationProcessor(
                pUserName, self._timekprConfig
            )

            # load config
            result, message = userControlProcessor.checkAndSetTimeLeft(
                pOperation, pTimeLeft
            )

            # check if we have this user
            if pUserName in self._timekprUserList:
                # inform the user immediately
                self._timekprUserList[pUserName].adjustTimeSpentFromControl(
                    pSilent=False, pPreserveSpent=(pOperation != "=")
                )
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONTROL_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    # --------------- server admin get methods accessible by privileged users (root and all in timekpr group) --------------- #

    @timekprAuthorizedMethod(
        cons.TK_DBUS_ADMIN_INTERFACE, "", "isa{sv}", cons.TK_POLKIT_ACTION_READ
    )
    def getTimekprConfiguration(self):
        """Get all timekpr configuration from server"""
        # default
        timekprConfig = {}
        try:
            # check the configuration
            mainConfigurationProcessor = timekprConfigurationProcessor()

            # check and set config
            result, message, timekprConfig = (
                mainConfigurationProcessor.getSavedTimekprConfiguration()
            )
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation("TK_MSG_CONFIG_LOADER_UNEXPECTED_ERROR")

        # result
        return result, message, timekprConfig

    # --------------- server admin set methods accessible by privileged users (root and all in timekpr group) --------------- #

    @timekprAuthorizedMethod(
        cons.TK_DBUS_ADMIN_INTERFACE, "i", "is", cons.TK_POLKIT_ACTION_SERVER_CONFIGURE
    )
    def setTimekprLogLevel(self, pLogLevel):
        """Set the logging level for server"""
        """ restart needed to fully engage, but newly logged in users get logging properly"""
        try:
            # check the configuration
            mainConfigurationProcessor = timekprConfigurationProcessor()

            # check and set config
            result, message = mainConfigurationProcessor.checkAndSetTimekprLogLevel(
                pLogLevel
            )

            # set in memory as well
            self._timekprConfig.setTimekprLogLevel(pLogLevel)
            # set it effective immediately
            log.setLogLevel(pLogLevel)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_ADMIN_INTERFACE, "i", "is", cons.TK_POLKIT_ACTION_SERVER_CONFIGURE
    )
    def setTimekprPollTime(self, pPollTimeSecs):
        """Set polltime for timekpr"""
        """ set in-memory polling time (this is the accounting precision of the time"""
        try:
            # check the configuration
            mainConfigurationProcessor = timekprConfigurationProcessor()

            # check and set config
            result, message = mainConfigurationProcessor.checkAndSetTimekprPollTime(
                pPollTimeSecs
            )

            # set in memory as well
            self._timekprConfig.setTimekprPollTime(pPollTimeSecs)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_ADMIN_INTERFACE, "i", "is", cons.TK_POLKIT_ACTION_SERVER_CONFIGURE
    )
    def setTimekprSaveTime(self, pSaveTimeSecs):
        """Set save time for timekpr"""
        """Set the interval at which timekpr saves user data (time spent, etc.)"""
        try:
            # check the configuration
            mainConfigurationProcessor = timekprConfigurationProcessor()

            # check and set config
            result, message = mainConfigurationProcessor.checkAndSetTimekprSaveTime(
                pSaveTimeSecs
            )

            # set in memory as well
            self._timekprConfig.setTimekprSaveTime(pSaveTimeSecs)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_ADMIN_INTERFACE, "b", "is", cons.TK_POLKIT_ACTION_SERVER_CONFIGURE
    )
    def setTimekprTrackInactive(self, pTrackInactive):
        """Set default value for tracking inactive sessions"""
        """Note that this is just the default value which is configurable at user level"""
        try:
            # check the configuration
            mainConfigurationProcessor = timekprConfigurationProcessor()

            # check and set config
            result, message = (
                mainConfigurationProcessor.checkAndSetTimekprTrackInactive(
                    pTrackInactive
                )
            )

            # set in memory as well
            self._timekprConfig.setTimekprTrackInactive(pTrackInactive)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_ADMIN_INTERFACE, "i", "is", cons.TK_POLKIT_ACTION_SERVER_CONFIGURE
    )
    def setTimekprTerminationTime(self, pTerminationTimeSecs):
        """Set up user termination time"""
        """ User temination time is how many seconds user is allowed in before he's thrown out
            This setting applies to users who log in at inappropriate time according to user config
        """
        try:
            # check the configuration
            mainConfigurationProcessor = timekprConfigurationProcessor()

            # check and set config
            result, message = (
                mainConfigurationProcessor.checkAndSetTimekprTerminationTime(
                    pTerminationTimeSecs
                )
            )

            # set in memory as well
            self._timekprConfig.setTimekprTerminationTime(pTerminationTimeSecs)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_ADMIN_INTERFACE, "i", "is", cons.TK_POLKIT_ACTION_SERVER_CONFIGURE
    )
    def setTimekprFinalWarningTime(self, pFinalWarningTimeSecs):
        """Set up final warning time for users"""
        """ Final warning time is the countdown lenght (in seconds) for the user before he's thrown out"""
        try:
            # check the configuration
            mainConfigurationProcessor = timekprConfigurationProcessor()

            # check and set config
            result, message = (
                mainConfigurationProcessor.checkAndSetTimekprFinalWarningTime(
                    pFinalWarningTimeSecs
                )
            )

            # set in memory as well
            self._timekprConfig.setTimekprFinalWarningTime(pFinalWarningTimeSecs)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_ADMIN_INTERFACE, "i", "is", cons.TK_POLKIT_ACTION_SERVER_CONFIGURE
    )
    def setTimekprFinalNotificationTime(self, pFinalNotificationTimeSecs):
        """Set up final warning time for users"""
        """ Final warning time is the countdown lenght (in seconds) for the user before he's thrown out"""
        try:
            # check the configuration
            mainConfigurationProcessor = timekprConfigurationProcessor()

            # check and set config
            result, message = (
                mainConfigurationProcessor.checkAndSetTimekprFinalNotificationTime(
                    pFinalNotificationTimeSecs
                )
            )

            # set in memory as well
            self._timekprConfig.setTimekprFinalNotificationTime(
                pFinalNotificationTimeSecs
            )
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_ADMIN_INTERFACE, "as", "is", cons.TK_POLKIT_ACTION_SERVER_CONFIGURE
    )
    def setTimekprSessionsCtrl(self, pSessionsCtrl):
        """Set accountable session types for users"""
        """ Accountable sessions are sessions which are counted as active, there are handful of them, but predefined"""
        try:
            # check the configuration
            mainConfigurationProcessor = timekprConfigurationProcessor()

            # check and set config
            result, message = mainConfigurationProcessor.checkAndSetTimekprSessionsCtrl(
                pSessionsCtrl
            )

            # set in memory as well
            self._timekprConfig.setTimekprSessionsCtrl(pSessionsCtrl)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_ADMIN_INTERFACE, "as", "is", cons.TK_POLKIT_ACTION_SERVER_CONFIGURE
    )
    def setTimekprSessionsExcl(self, pSessionsExcl):
        """Set NON-accountable session types for users"""
        """ NON-accountable sessions are sessions which are explicitly ignored during session evaluation, there are handful of them, but predefined"""
        try:
            # result
            # check the configuration
            mainConfigurationProcessor = timekprConfigurationProcessor()

            # check and set config
            result, message = mainConfigurationProcessor.checkAndSetTimekprSessionsExcl(
                pSessionsExcl
            )

            # set in memory as well
            self._timekprConfig.setTimekprSessionsExcl(pSessionsExcl)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message

    @timekprAuthorizedMethod(
        cons.TK_DBUS_ADMIN_INTERFACE, "as", "is", cons.TK_POLKIT_ACTION_SERVER_CONFIGURE
    )
    def setTimekprUsersExcl(self, pUsersExcl):
        """Set excluded usernames for timekpr"""
        """ Excluded usernames are usernames which are excluded from accounting
            Pre-defined values containt all graphical login managers etc., please do NOT add actual end-users here,
            You can, but these users will never receive any notifications about time, icon will be in connecting state forever
        """
        try:
            # check the configuration
            mainConfigurationProcessor = timekprConfigurationProcessor()

            # check and set config
            result, message = mainConfigurationProcessor.checkAndSetTimekprUsersExcl(
                pUsersExcl
            )

            # set in memory as well
            self._timekprConfig.setTimekprUsersExcl(pUsersExcl)
        except Exception as unexpectedException:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"Unexpected ERROR ({misc.whoami()}): {unexpectedException!s}",
            )

            # result
            result = -1
            message = msg.getTranslation(
                "TK_MSG_CONFIG_LOADER_SAVECONFIG_UNEXPECTED_ERROR"
            )

        # result
        return result, message
