"""
Created on Aug 28, 2018

@author: mjasnik
"""

# imports
import getpass
import os
import shutil
import subprocess
import sys
from os import geteuid

from timekpr.client.interface.dbus.administration import timekprAdminConnector

# timekpr imports
from timekpr.common.constants import constants as cons
from timekpr.common.constants import messages as msg
from timekpr.common.log import log
from timekpr.common.utils import cmdhelp
from timekpr.common.utils.config import timekprConfig
from timekpr.common.utils.misc import findHourStartEndMinutes


class timekprAdminClient:
    """Main class for holding all client logic (including dbus)"""

    # --------------- initialization / control methods --------------- #

    def __init__(self):
        """Initialize admin client"""
        # get our connector
        # main connector (chosen when the arguments are known)
        self._timekprAdminConnector = None

        # main object for GUI
        self._adminGUI = None

    def extractOption(self, pArgs, pOption, pDefault):
        """Remove "--option VALUE" or "--option=VALUE" from the arguments, returning (arguments, value)"""
        args = list(pArgs)
        value = pDefault
        idx = 0
        while idx < len(args):
            if args[idx] == pOption:
                if idx + 1 >= len(args):
                    log.consoleOut(f"{pOption} needs a value")
                    sys.exit(1)
                value = args[idx + 1]
                del args[idx : idx + 2]
            elif args[idx].startswith(pOption + "="):
                value = args[idx][len(pOption) + 1 :]
                del args[idx]
            else:
                idx += 1
        return args, value

    def startTimekprAdminClient(self, *args):
        """Start up timekpr admin (choose gui or cli and start this up)"""
        # help is served before anything else: it needs no configuration, no log file and no connection to the daemon
        if cmdhelp.isHelpRequested(args[1:]):
            # print help and get out
            cmdhelp.printAdminHelp()
            return

        # talk to timekprw over HTTP instead of to the daemon over D-Bus?
        args, server = self.extractOption(
            args, "--server", os.getenv("TIMEKPRA_SERVER")
        )
        args, tokenFile = self.extractOption(
            args, "--token-file", os.getenv("TIMEKPRA_TOKEN_FILE")
        )
        if server:
            from timekpr.client.interface.http.administration import (
                timekprAdminHttpConnector,
            )

            self._timekprAdminConnector = timekprAdminHttpConnector(server, tokenFile)
        else:
            self._timekprAdminConnector = timekprAdminConnector()
        # check whether we need CLI or GUI
        lastParam = args[len(args) - 1]
        timekprForceCLI = False

        # configuration init
        _timekprConfig = timekprConfig()
        # load config
        _timekprConfig.loadMainConfiguration()

        # init logging
        log.setLogging(
            _timekprConfig.getTimekprLogLevel(),
            cons.TK_LOG_TEMP_DIR,
            (cons.TK_LOG_OWNER_ADMIN_SU if geteuid() == 0 else cons.TK_LOG_OWNER_ADMIN),
            getpass.getuser(),
        )

        # check for script
        if server:
            # the GUI talks D-Bus only
            timekprForceCLI = True
        elif "/timekpra" in lastParam or "timekpra.py" in lastParam:
            # whether we have X running or wayland?
            timekprX11Available = os.getenv("DISPLAY") is not None
            timekprWaylandAvailable = os.getenv("WAYLAND_DISPLAY") is not None
            timekprMirAvailable = os.getenv("MIR_SOCKET") is not None

            # if we are required to run graphical thing
            if timekprX11Available or timekprWaylandAvailable or timekprMirAvailable:
                # resource dir
                _resourcePathGUI = os.path.join(
                    _timekprConfig.getTimekprSharedDir(), "client/forms"
                )
                # use GUI
                from timekpr.client.gui.admingui import timekprAdminGUI

                # load GUI and process from there
                self._adminGUI = timekprAdminGUI(
                    cons.TK_VERSION, _resourcePathGUI, getpass.getuser()
                )
                # start GUI
                self._adminGUI.startAdminGUI()
            # nor X nor wayland are available
            else:
                # print to console
                log.consoleOut(
                    "{}\n".format(
                        msg.getTranslation("TK_MSG_CONSOLE_GUI_NOT_AVAILABLE")
                    )
                )
                # forced CLI"
                timekprForceCLI = True
        else:
            # CLI
            timekprForceCLI = True

        # for CLI connections
        if timekprForceCLI:
            # the server asks polkit before doing anything, and polkit needs an agent to ask us for a password
            ttyAgent = self.startTtyAuthenticationAgent()
            try:
                # connect
                self._timekprAdminConnector.initTimekprConnection(True)
                # connected?
                if self._timekprAdminConnector.isConnected()[1]:
                    # use CLI
                    # validate possible parameters and their values, when fine - execute them as well
                    self.checkAndExecuteAdminCommands(*args)
                    log.flushLogFile()
            finally:
                self.stopTtyAuthenticationAgent(ttyAgent)

    # --------------- initialization / helper methods --------------- #

    def startTtyAuthenticationAgent(self):
        """Register polkit's text mode authentication agent for this process, if there is a terminal to ask on"""
        agent = None
        # no agent without a terminal or without polkit
        if not sys.stdin.isatty() or shutil.which("pkttyagent") is None:
            return None
        try:
            # pkttyagent closes the notify fd once it is registered (or exits, which closes it too)
            readFd, writeFd = os.pipe()
            agent = subprocess.Popen(
                [
                    "pkttyagent",
                    "--process",
                    str(os.getpid()),
                    "--notify-fd",
                    str(writeFd),
                    "--fallback",
                ],
                pass_fds=(writeFd,),
            )
            os.close(writeFd)
            os.read(readFd, 1)
            os.close(readFd)
        except Exception as ex:
            # without an agent authorization simply fails for those who need to authenticate
            log.log(cons.TK_LOG_LEVEL_INFO, f"could not start pkttyagent: {ex!s}")
        return agent

    def stopTtyAuthenticationAgent(self, pAgent):
        """Stop the agent started by startTtyAuthenticationAgent"""
        if pAgent is not None and pAgent.poll() is None:
            pAgent.terminate()
            pAgent.wait()

    def finishTimekpr(self, signal=None, frame=None):
        """Exit timekpr admin GUI gracefully"""
        if self._adminGUI is not None:
            # finish main thread on GUI`
            self._adminGUI.finishTimekpr(signal, frame)

    # --------------- parameter validation methods --------------- #

    def checkAndExecuteAdminCommands(self, *args):
        """Init connection to timekpr dbus server"""
        # initial param len
        paramIdx = 0
        paramLen = len(args)
        adminCmdIncorrect = False

        # determine parameter offset
        for tmpIdx, rArg in enumerate(args, start=1):
            # check for script
            if "/timekpra" in rArg or "timekpra.py" in rArg:
                paramIdx = tmpIdx

        # this gets the command itself (args[0] is the script name)
        adminCmd = args[paramIdx] if paramLen > paramIdx else "timekpra"

        # now based on params check them out
        # this gets saved user list from the server
        if adminCmd == "--help":
            # fine
            pass
        # this gets saved user list from the server
        elif adminCmd == "--userlist":
            # check param len
            if paramLen != paramIdx + 1:
                # fail
                adminCmdIncorrect = True
            else:
                # get list
                result, message, userList = self._timekprAdminConnector.getUserList()

                # process
                if result == 0:
                    # process
                    self.printUserList(userList)
                else:
                    # log error
                    log.consoleOut(message)
        # this gets user configuration from the server
        elif adminCmd == "--userinfo":
            # check param len
            if paramLen != paramIdx + 2:
                # fail
                adminCmdIncorrect = True
            else:
                # get user config
                result, message, userConfig = (
                    self._timekprAdminConnector.getUserConfigurationAndInformation(
                        args[paramIdx + 1], cons.TK_CL_INF_FULL
                    )
                )

                # process
                if result == 0:
                    # process
                    self.printUserConfig(args[paramIdx + 1], userConfig)
                else:
                    # log error
                    log.consoleOut(message)
        # this gets user configuration from the server
        elif adminCmd == "--userinfort":
            # check param len
            if paramLen != paramIdx + 2:
                # fail
                adminCmdIncorrect = True
            else:
                # get user config
                result, message, userConfig = (
                    self._timekprAdminConnector.getUserConfigurationAndInformation(
                        args[paramIdx + 1], cons.TK_CL_INF_RT
                    )
                )

                # process
                if result == 0:
                    # process
                    self.printUserConfig(args[paramIdx + 1], userConfig)
                else:
                    # log error
                    log.consoleOut(message)
        # this sets allowed days for the user
        elif adminCmd == "--setalloweddays":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                self.processSetAllowedDays(args[paramIdx + 1], args[paramIdx + 2])
        # this sets allowed hours per specified day or ALL for every day
        elif adminCmd == "--setallowedhours":
            # check param len
            if paramLen != paramIdx + 4:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                self.processSetAllowedHours(
                    args[paramIdx + 1], args[paramIdx + 2], args[paramIdx + 3]
                )
        # this sets time limits per allowed days
        elif adminCmd == "--settimelimits":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                self.processSetTimeLimits(args[paramIdx + 1], args[paramIdx + 2])
        # this sets time limits per week
        elif adminCmd == "--settimelimitweek":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                self.processSetTimeLimitWeek(args[paramIdx + 1], args[paramIdx + 2])
        # this sets time limits per month
        elif adminCmd == "--settimelimitmonth":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                self.processSetTimeLimitMonth(args[paramIdx + 1], args[paramIdx + 2])
        # this sets whether to track inactive user sessions
        elif adminCmd == "--settrackinactive":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                self.processSetTrackInactive(args[paramIdx + 1], args[paramIdx + 2])
        # this sets whether to show tray icon
        elif adminCmd == "--sethidetrayicon":
            # check param len
            if paramLen != paramIdx + 3:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                self.processSetHideTrayIcon(args[paramIdx + 1], args[paramIdx + 2])
        # this sets time left for the user at current moment
        elif adminCmd == "--settimeleft":
            # check param len
            if paramLen != paramIdx + 4:
                # fail
                adminCmdIncorrect = True
            else:
                # set days
                self.processSetTimeLeft(
                    args[paramIdx + 1], args[paramIdx + 2], args[paramIdx + 3]
                )
        else:
            # out
            adminCmdIncorrect = True

        # check whether command is supported
        if (
            (
                adminCmd not in cons.TK_USER_ADMIN_COMMANDS
                and adminCmd not in cons.TK_ADMIN_COMMANDS
            )
            or adminCmd == "--help"
            or adminCmdIncorrect
        ):
            # fail
            if adminCmdIncorrect:
                log.consoleOut(
                    msg.getTranslation("TK_MSG_CONSOLE_COMMAND_INCORRECT"), *args, "\n"
                )

            # print help
            cmdhelp.printAdminHelp()

    # --------------- parameter execution methods --------------- #

    def printUserList(self, pUserList):
        """Format and print userlist"""
        # print to console
        log.consoleOut(msg.getTranslation("TK_MSG_CONSOLE_USERS_TOTAL", len(pUserList)))
        # loop and print
        for rUser in pUserList:
            log.consoleOut(rUser[0])

    def printUserConfig(self, pUserName, pPrintUserConfig):
        """Format and print user config"""
        # print to console
        log.consoleOut(
            "# %s" % (msg.getTranslation("TK_MSG_CONSOLE_CONFIG_FOR") % (pUserName))
        )
        # loop and print the same format as ppl will use to set that
        for rUserKey, rUserConfig in pPrintUserConfig.items():
            # join the lists
            if rUserKey in ("ALLOWED_WEEKDAYS", "LIMITS_PER_WEEKDAYS"):
                # print join
                log.consoleOut(
                    "{}: {}".format(rUserKey, ";".join(list(map(str, rUserConfig))))
                )
            # join the lists
            elif "ALLOWED_HOURS_" in rUserKey:
                # hrs
                hrs = ""
                # print join
                if len(rUserConfig) > 0:
                    # process hours
                    for rUserHour in sorted(map(int, rUserConfig)):
                        # unaccounted hour
                        uacc = (
                            "!"
                            if rUserConfig[str(rUserHour)][cons.TK_CTRL_UACC]
                            else ""
                        )
                        # get config per hr
                        hr = (
                            f"{rUserHour}"
                            if rUserConfig[str(rUserHour)][cons.TK_CTRL_SMIN] <= 0
                            and rUserConfig[str(rUserHour)][cons.TK_CTRL_EMIN] >= 60
                            else f"{rUserHour}[{rUserConfig[str(rUserHour)][cons.TK_CTRL_SMIN]}-{rUserConfig[str(rUserHour)][cons.TK_CTRL_EMIN]}]"
                        )
                        # empty
                        hrs = f"{uacc}{hr}" if hrs == "" else f"{hrs};{uacc}{hr}"
                log.consoleOut(f"{rUserKey}: {hrs}")
            elif rUserKey in ("TRACK_INACTIVE", "HIDE_TRAY_ICON"):
                log.consoleOut(f"{rUserKey}: {bool(rUserConfig)}")
            else:
                log.consoleOut(f"{rUserKey}: {rUserConfig!s}")

    def processSetAllowedDays(self, pUserName, pDayList):
        """Process allowed days"""
        # defaults
        dayMap = []
        result = 0

        # day map
        try:
            # try to parse parameters
            dayMap = pDayList.split(";")
        except Exception as ex:
            # fail
            result = -1
            message = msg.getTranslation("TK_MSG_PARSE_ERROR") % (str(ex))

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setAllowedDays(
                pUserName, dayMap
            )

        # process
        if result != 0:
            # log error
            log.consoleOut(message)

    def processSetAllowedHours(self, pUserName, pDayNumber, pHourList):
        """Process allowed hours"""
        # this is the dict for hour config
        allowedHours = {}
        result = 0

        # allowed hours
        try:
            # check hours
            for rHour in str(pHourList).split(";"):
                # get hours and minutes
                hour, sMin, eMin, uacc = findHourStartEndMinutes(rHour)
                # raise any error in case we can not get parsing right
                if hour is None:
                    # raise
                    raise ValueError("this does not compute")
                # set hours
                allowedHours[str(hour)] = {
                    cons.TK_CTRL_SMIN: sMin,
                    cons.TK_CTRL_EMIN: eMin,
                    cons.TK_CTRL_UACC: uacc,
                }
        except Exception as ex:
            # fail
            result = -1
            message = msg.getTranslation("TK_MSG_PARSE_ERROR") % (str(ex))

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setAllowedHours(
                pUserName, pDayNumber, allowedHours
            )

        # process
        if result != 0:
            # log error
            log.consoleOut(message)

    def processSetTimeLimits(self, pUserName, pDayLimits):
        """Process time limits for days"""
        # defaults
        dayLimits = []
        result = 0

        # day limists
        try:
            # allow empty limits too
            if str(pDayLimits) != "":
                # try to parse parameters
                dayLimits = list(map(int, pDayLimits.split(";")))
        except Exception as ex:
            # fail
            result = -1
            message = msg.getTranslation("TK_MSG_PARSE_ERROR") % (str(ex))

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setTimeLimitForDays(
                pUserName, dayLimits
            )

        # process
        if result != 0:
            # log error
            log.consoleOut(message)

    def processSetTimeLimitWeek(self, pUserName, pTimeLimitWeek):
        """Process time limits for week"""
        # defaults
        weekLimit = 0
        result = 0

        # week limit
        try:
            # try to parse parameters
            weekLimit = int(pTimeLimitWeek)
        except Exception as ex:
            # fail
            result = -1
            message = msg.getTranslation("TK_MSG_PARSE_ERROR") % (str(ex))

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setTimeLimitForWeek(
                pUserName, weekLimit
            )

        # process
        if result != 0:
            # log error
            log.consoleOut(message)

    def processSetTimeLimitMonth(self, pUserName, pTimeLimitMonth):
        """Process time limits for month"""
        # defaults
        monthLimit = 0
        result = 0

        # week limit
        try:
            # try to parse parameters
            monthLimit = int(pTimeLimitMonth)
        except Exception as ex:
            # fail
            result = -1
            message = msg.getTranslation("TK_MSG_PARSE_ERROR") % (str(ex))

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setTimeLimitForMonth(
                pUserName, monthLimit
            )

        # process
        if result != 0:
            # log error
            log.consoleOut(message)

    def processSetTrackInactive(self, pUserName, pTrackInactive):
        """Process track inactive"""
        # defaults
        trackInactive = None
        result = 0

        # check
        if str(pTrackInactive).lower() not in ("true", "false"):
            # fail
            result = -1
            message = msg.getTranslation("TK_MSG_PARSE_ERROR") % (
                "please specify true or false"
            )
        else:
            trackInactive = str(pTrackInactive).lower() == "true"

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setTrackInactive(
                pUserName, trackInactive
            )

        # process
        if result != 0:
            # log error
            log.consoleOut(message)

    def processSetHideTrayIcon(self, pUserName, pHideTrayIcon):
        """Process hide tray icon"""
        # defaults
        hideTrayIcon = None
        result = 0

        # check
        if str(pHideTrayIcon).lower() not in ("true", "false"):
            # fail
            result = -1
            message = msg.getTranslation("TK_MSG_PARSE_ERROR") % (
                "please specify true or false"
            )
        else:
            hideTrayIcon = str(pHideTrayIcon).lower() == "true"

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setHideTrayIcon(
                pUserName, hideTrayIcon
            )

        # process
        if result != 0:
            # log error
            log.consoleOut(message)

    def processSetTimeLeft(self, pUserName, pOperation, pLimit):
        """Process time left"""
        # defaults
        limit = 0
        result = 0

        # limit
        try:
            # try to parse parameters
            limit = int(pLimit)
        except Exception as ex:
            # fail
            result = -1
            message = msg.getTranslation("TK_MSG_PARSE_ERROR") % (str(ex))

        # preprocess successful
        if result == 0:
            # invoke
            result, message = self._timekprAdminConnector.setTimeLeft(
                pUserName, pOperation, limit
            )

        # process
        if result != 0:
            # log error
            log.consoleOut(message)
