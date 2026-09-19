"""
Created on Aug 28, 2018

@author: mjasnik
"""

# defaults
_START_TIME = None
_END_TIME = None
_RESULT = 0

# imports
import inspect
import os
import pwd
import stat
from datetime import datetime

from gi.repository import Gio

# timekpr imports
from timekpr.common.constants import constants as cons
from timekpr.common.log import log


# this is needed for debugging purposes
def whoami():
    """Return callers name from the call stack, the 0 is this function, prev is the one needd"""
    return inspect.stack()[1][3]


def getDBUSUserName(pUserName):
    """Get user name suitable for use as a DBUS object path element (only [A-Za-z0-9_] are allowed there)"""
    # domain users (user@domain) and machine accounts (user$) contain characters which are not allowed in object paths,
    #   GLib escapes those losslessly (e.g. "bob@idm" becomes "bob_40idm"), so different user names never clash
    return Gio.dbus_escape_object_path(pUserName)


def getNormalizedUserNames(pUID=None, pUser=None):
    """Get usernames and/or normalize them"""
    user = pUser
    userName = None
    userNameFull = ""

    try:
        # if we need to get one
        if pUID is not None:
            # user
            user = pwd.getpwuid(pUID)
        # we have user
        if user is not None:
            # username
            userName = user.pw_name
            userNameFull = user.pw_gecos
        # workaround for distros that have one or more "," at the end of user full name
        userNameFull = userNameFull.rstrip(",")
        # if username is exactly the same as full name, no need to show it separately
        userNameFull = userNameFull if userNameFull != userName else ""
    except KeyError:
        pass

    # full username
    return userName, userNameFull


def measureTimeElapsed(pStart=False, pStop=False, pResult=False):
    """Calculate the time difference in the simplest manner"""
    # init globals (per import)
    global _START_TIME
    global _END_TIME
    global _RESULT

    # set up start
    if pStart:
        _START_TIME = datetime.now()
    # set up end
    if pStop:
        # calc seconds and finish stuff
        _END_TIME = datetime.now()
        _RESULT = (_END_TIME - _START_TIME).total_seconds()
        _START_TIME = _END_TIME

    # return
    return _RESULT


def measureDBUSTimeElapsed(
    pStart=False, pStop=False, pPrintToConsole=False, pDbusIFName=""
):
    """Calculate the time difference in the simplest manner"""
    # run
    result = measureTimeElapsed(pStart, pStop)
    # in case we measure dbus performance issues, just print them
    if pStop and result >= cons.TK_DBUS_ANSWER_TIME:
        # measurement logging
        if pPrintToConsole:
            # measurement logging
            log.consoleOut(
                f'WARNING: PERFORMANCE (DBUS) - acquiring "{pDbusIFName}" took too long ({int(result)}s)'
            )
        else:
            # measurement logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f'WARNING: PERFORMANCE (DBUS) - acquiring "{pDbusIFName}" took too long ({int(result)}s)',
            )

    # return
    return result


def checkAndSetRunning(pAppName, pUserName=""):
    """Check whether application is already running"""
    # set up pidfile name
    pidFile = os.path.join(
        cons.TK_LOG_TEMP_DIR,
        "{}.{}".format(
            (pAppName if pUserName == "" else f"{pAppName}.{pUserName}"),
            cons.TK_LOG_PID_EXT,
        ),
    )
    processPid = "0"
    processCmd = ""
    isAlreadyRunning = False
    isWritable = True

    # check if we have pid file for the app
    if os.path.isfile(pidFile):
        # check stats
        fileStat = os.stat(pidFile)
        # check permissions
        isWritable = stat.S_IWUSR & fileStat.st_mode and fileStat.st_uid == os.getuid()

        # if file is not ours, we don't even try to read from it
        if isWritable:
            # if we have a file, we read the pid from there
            with open(pidFile, "r") as pidfiler:
                # determine pid
                processPid = pidfiler.readline(10).rstrip("\n").rstrip("\r")
                # check if pid is numeric
                processPid = "0" if not processPid.isnumeric() else processPid

            # so we have a running app, now we check whether its our app
            if processPid != "0":
                # get process commandline
                procPidFile = os.path.join("/proc", processPid, "cmdline")

                # check whether we have a process running with this pid
                if os.path.isfile(procPidFile):
                    # we wrap this with try in case pid is very short-lived
                    try:
                        with open(procPidFile, "r") as pidfile:
                            processCmd = pidfile.readline()
                    except Exception:
                        processCmd = ""

    # check if this is our process
    if pAppName in processCmd:
        # we are running
        isAlreadyRunning = True
        # print this to console as well
        print(
            'Timekpr-nExT "{}" is already running for user "{}"'.format(
                pAppName, pUserName if pUserName != "" else "root"
            )
        )
    else:
        # check if we have pid file and it is a link for some reason
        if os.path.islink(pidFile) or not isWritable:
            # remove the "old" pid file
            os.remove(pidFile)
        # set our pid
        with open(pidFile, "w") as pidfilew:
            # write our pid to pid file
            processCmd = pidfilew.write(str(os.getpid()))

    # return whether we are running
    return isAlreadyRunning


def findHourStartEndMinutes(pStr):
    """Separate name and desription in brackets"""
    # hour, start, end
    hour = None
    sMin = None
    eMin = None
    uacc = None

    # get len beforehand
    ln = len(pStr) if pStr is not None else 0

    # it makes sense to calc stuff only when there is a hour defined
    if ln > 0:
        # is hour unaccounted
        uacc = pStr[0] == "!"
        # in case of unlimited hour actual len is smaller
        ln = ln - 1 if uacc else ln
        # get hour (ex: 1 or 11)
        if 1 <= ln <= 2:
            # hour, start, end
            hour = pStr[1:] if uacc else pStr
            sMin = 0
            eMin = 60
        # get hours and minutes (ex: 1[1:1] or 11[11:22])
        elif 6 <= ln <= 9:
            # find minutes
            beg = 1 if uacc else 0
            st = pStr.find("[")
            sep = pStr.find("-")
            # failover to : (currently undocumented)
            sep = sep if not sep < 0 else pStr.find(":")
            en = pStr.find("]")
            # in case user config is broken, we cannot determine stuff
            if st < 0 or en < 0 or sep < 0 or not st < sep < en:
                # nothing
                pass
            else:
                # hour, start, end
                try:
                    # determine hour and minutes (and check for errors as well)
                    hour = int(pStr[beg:st])
                    sMin = int(pStr[st + 1 : sep])
                    eMin = int(pStr[sep + 1 : en])
                    # checks for errors (and raise one if there is an error)
                    hour = hour if 0 <= hour <= 23 else 1 / 0
                    sMin = sMin if 0 <= sMin <= 60 else 1 / 0
                    eMin = eMin if 0 <= eMin <= 60 else 1 / 0
                    eMin = eMin if sMin < eMin else 1 / 0
                except (ValueError, ZeroDivisionError):
                    # hour, start, end
                    hour = None
                    sMin = None
                    eMin = None
                    uacc = None

    # return
    return hour, sMin, eMin, uacc


def splitConfigValueNameParam(pStr):
    """Separate value and param in brackets"""
    # name and its value
    value = None
    param = None

    # nothing
    if len(pStr) < 2:
        # can not be a normal value
        pass
    else:
        try:
            # find description ("") is for backwards compatibility
            st = pStr.find('("')  # compatibility description start
            en = pStr.find('")')  # compatibility description end
            ln = (
                1 if st < 0 else 2
            )  # compatility case searches for 2 letters, new one 1
            # new style config
            st = pStr.find("[") if st < 0 else st  # new style config
            en = pStr.find("]") if en < 0 else en  # new style config
            st = en if st < 0 else st  # no description, we'll get just pattern
            # process and its description
            value = pStr[0 : st if st > 0 else len(pStr)]
            param = "" if st < 0 else pStr[st + ln : en if en >= 0 else len(pStr)]
        except Exception:
            # it doesn't matter which error occurs
            value = None
            param = None

    # return
    return value, param
