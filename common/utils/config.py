"""
Created on Aug 28, 2018

@author: mjasnik
"""

# imports
import configparser
import os
import re
import shutil
from datetime import datetime

from timekpr.common.constants import constants as cons

# timekpr imports
from timekpr.common.log import log
from timekpr.common.utils.misc import (
    findHourStartEndMinutes,
    splitConfigValueNameParam,
)

# ## GLOBAL ##
# key pattern search
RE_KEYFINDER = re.compile("^ *([A-Z]+[A-Z_]+[0-9]*) *=.*$")
RE_ARRAYKEYFINDER = re.compile("^##([A-Z]+[A-Z_]+)##.*$")


def _saveConfigFile(pConfigFile, pKeyValuePairs):
    """Save the config file using custom helper function"""
    # edit control file (using alternate method because configparser looses comments in the process)
    # make a backup of the file
    shutil.copy(pConfigFile, pConfigFile + cons.TK_BACK_EXT)
    # read backup and write actual config file
    with (
        open(pConfigFile + cons.TK_BACK_EXT, "r") as srcFile,
        open(pConfigFile, "w") as dstFile,
    ):
        # destination file
        dstLines = []
        # read line and do manipulations
        for rLine in srcFile:
            # def line
            line = rLine
            # if line matches parameter pattern, we look up for that key in our value list
            if RE_KEYFINDER.match(rLine):
                # check whether we can find the value for it
                key = RE_KEYFINDER.sub(r"\1", rLine.rstrip())
                # if key exists
                if key in pKeyValuePairs:
                    # in case of placeholder (value = None), just keep the line, else replace it
                    if pKeyValuePairs[key] is not None:
                        # now get the value
                        dstLines.append(f"{key} = {pKeyValuePairs[key]}\n")
                        # do not add original line
                        line = None
                else:
                    # do not add unknown options
                    line = None
            # search for variable options
            elif RE_ARRAYKEYFINDER.match(rLine):
                # check whether we can find the value for it
                key = RE_ARRAYKEYFINDER.sub(r"\1", rLine.rstrip())
                # now get the value
                dstLines.append(f"{rLine}")
                # if key exists
                if key in pKeyValuePairs:
                    # append array of values
                    for rVal in pKeyValuePairs[key]:
                        # now get the value
                        dstLines.append(f"{rVal}\n")
                # do not add original line
                line = None

            # append if there is a line
            if line is not None:
                # add line
                dstLines.append(line)

        # save config lines back to file
        dstFile.writelines(dstLines)


def _loadAndPrepareConfigFile(pConfigFileParser, pConfigFile, pLoadOnly=False):
    """Try to load config file, if that fails, try to read backup file"""
    # by default fail
    result = False
    # process primary and backup files
    for rFile in (pConfigFile, pConfigFile + cons.TK_BACK_EXT):
        # if file is ok
        if os.path.isfile(rFile) and os.path.getsize(rFile) != 0:
            # copy file back to original (if this is backup file)
            if rFile != pConfigFile and not pLoadOnly:
                shutil.copy(rFile, pConfigFile)
            # read config
            try:
                # read config file
                pConfigFileParser.read(pConfigFile)
                # success
                result = True
                break
            except Exception:
                # not load only
                if not pLoadOnly:
                    # fail, move corrupted file
                    os.rename(rFile, f"{rFile}.invalid")
        else:
            # we do not need empty files
            if os.path.isfile(rFile) and not pLoadOnly:
                # remove empty file
                os.remove(rFile)

    # result
    return result


def _readAndNormalizeValue(
    pConfigFileParserFn, pSection, pParam, pDefaultValue, pCheckValue, pOverallSuccess
):
    """Read value from parser, if fails, then return default value"""
    # default values
    result = pOverallSuccess
    value = pDefaultValue
    try:
        # read value from parser
        value = pConfigFileParserFn(pSection, pParam)
        # check min / max if we have numbers
        if pCheckValue is not None and type(pDefaultValue).__name__ in ("int", "float"):
            value = int(min(max(value, -pCheckValue), pCheckValue))
        # validate date format properly
        elif type(pDefaultValue).__name__ in ("date", "datetime"):
            value = datetime.strptime(value, cons.TK_DATETIME_FORMAT)
    except Exception:
        # default value
        value = pDefaultValue
        # failed
        result = False

    # return
    return result, value


def _cleanupValue(pValue):
    """Clean up value (basically remove stuff from begining and end)"""
    return pValue.strip().strip(";") if pValue is not None else None


class timekprConfig:
    """Main configuration class for the server"""

    def __init__(self):
        """Initialize stuff"""
        log.log(cons.TK_LOG_LEVEL_INFO, "initializing configuration manager")

        # config
        self._timekprConfig = {}

        # in dev
        self._configDirPrefix = os.getcwd() if cons.TK_DEV_ACTIVE else ""
        # main config
        self._timekprConfig["TIMEKPR_MAIN_CONFIG_DIR"] = os.path.join(
            self._configDirPrefix,
            (
                cons.TK_MAIN_CONFIG_DIR_DEV
                if cons.TK_DEV_ACTIVE
                else cons.TK_MAIN_CONFIG_DIR
            ),
        )
        self._configFile = os.path.join(
            self._timekprConfig["TIMEKPR_MAIN_CONFIG_DIR"], cons.TK_MAIN_CONFIG_FILE
        )

        # config parser
        self._timekprConfigParser = configparser.ConfigParser(allow_no_value=True)
        self._timekprConfigParser.optionxform = str

        log.log(cons.TK_LOG_LEVEL_INFO, "finish configuration manager")

    def __del__(self):
        """De-initialize stuff"""
        log.log(cons.TK_LOG_LEVEL_INFO, "de-initializing configuration manager")

    def loadMainConfiguration(self):
        """Read main timekpr config file"""
        log.log(cons.TK_LOG_LEVEL_DEBUG, "start loading configuration")

        # try to load config file
        result = _loadAndPrepareConfigFile(self._timekprConfigParser, self._configFile)
        # value read result
        resultValue = True

        # read config failed, we need to initialize
        if not result:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"ERROR: could not parse main configuration file ({self._configFile}) properly, will use default values",
            )
            # init config
            self.initDefaultConfiguration()
            # re-read the file
            self._timekprConfigParser.read(self._configFile)
            # config initialized
            result = True

        # general section
        section = "GENERAL"
        # read
        param = "TIMEKPR_VERSION"
        self._timekprConfig[param] = cons.TK_VERSION
        # read
        param = "TIMEKPR_LOGLEVEL"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(
            self._timekprConfigParser.getint,
            section,
            param,
            pDefaultValue=cons.TK_LOG_LEVEL_INFO,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        # read
        param = "TIMEKPR_POLLTIME"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(
            self._timekprConfigParser.getint,
            section,
            param,
            pDefaultValue=cons.TK_POLLTIME,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        # read
        param = "TIMEKPR_SAVE_TIME"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(
            self._timekprConfigParser.getint,
            section,
            param,
            pDefaultValue=cons.TK_SAVE_INTERVAL,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        # read
        param = "TIMEKPR_TRACK_INACTIVE"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(
            self._timekprConfigParser.getboolean,
            section,
            param,
            pDefaultValue=cons.TK_TRACK_INACTIVE,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        # read
        param = "TIMEKPR_TERMINATION_TIME"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(
            self._timekprConfigParser.getint,
            section,
            param,
            pDefaultValue=cons.TK_TERMINATION_TIME,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        # read
        param = "TIMEKPR_FINAL_WARNING_TIME"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(
            self._timekprConfigParser.getint,
            section,
            param,
            pDefaultValue=cons.TK_FINAL_COUNTDOWN_TIME,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        # read
        param = "TIMEKPR_FINAL_NOTIFICATION_TIME"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(
            self._timekprConfigParser.getint,
            section,
            param,
            pDefaultValue=cons.TK_FINAL_NOTIFICATION_TIME,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )

        # session section
        section = "SESSION"
        # read
        param = "TIMEKPR_SESSION_TYPES_CTRL"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(
            self._timekprConfigParser.get,
            section,
            param,
            pDefaultValue=cons.TK_SESSION_TYPES_CTRL,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        self._timekprConfig[param] = _cleanupValue(self._timekprConfig[param])
        # read
        param = "TIMEKPR_SESSION_TYPES_EXCL"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(
            self._timekprConfigParser.get,
            section,
            param,
            pDefaultValue=cons.TK_SESSION_TYPES_EXCL,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        self._timekprConfig[param] = _cleanupValue(self._timekprConfig[param])
        # read
        param = "TIMEKPR_USERS_EXCL"
        resultValue, self._timekprConfig[param] = _readAndNormalizeValue(
            self._timekprConfigParser.get,
            section,
            param,
            pDefaultValue=cons.TK_USERS_EXCL,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        self._timekprConfig[param] = _cleanupValue(self._timekprConfig[param])

        # directory section (! in case directories are not correct, they are not overwritten with defaults !)
        section = "DIRECTORIES"
        # read
        param = "TIMEKPR_CONFIG_DIR"
        result, value = _readAndNormalizeValue(
            self._timekprConfigParser.get,
            section,
            param,
            pDefaultValue=cons.TK_CONFIG_DIR,
            pCheckValue=None,
            pOverallSuccess=result,
        )
        self._timekprConfig[param] = os.path.join(self._configDirPrefix, value)
        # read
        param = "TIMEKPR_WORK_DIR"
        result, value = _readAndNormalizeValue(
            self._timekprConfigParser.get,
            section,
            param,
            pDefaultValue=cons.TK_WORK_DIR,
            pCheckValue=None,
            pOverallSuccess=result,
        )
        self._timekprConfig[param] = os.path.join(self._configDirPrefix, value)
        # read
        param = "TIMEKPR_SHARED_DIR"
        result, value = _readAndNormalizeValue(
            self._timekprConfigParser.get,
            section,
            param,
            pDefaultValue=cons.TK_SHARED_DIR,
            pCheckValue=None,
            pOverallSuccess=result,
        )
        self._timekprConfig[param] = os.path.join(self._configDirPrefix, value)
        # read
        param = "TIMEKPR_LOGFILE_DIR"
        result, value = _readAndNormalizeValue(
            self._timekprConfigParser.get,
            section,
            param,
            pDefaultValue=cons.TK_LOGFILE_DIR,
            pCheckValue=None,
            pOverallSuccess=result,
        )
        self._timekprConfig[param] = os.path.join(self._configDirPrefix, value)

        # if we could not read some values, save what we could + defaults
        if not resultValue:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"WARNING: some values in main config file ({self._configFile}) could not be read or new configuration option was introduced, valid values and defaults are used / saved instead",
            )
            # save what we could
            self.initDefaultConfiguration(True)

        # if we could not read some values, report that (directories only)
        if not result:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"ERROR: some directory values in main config file ({self._configFile}) could not be read, valid values and defaults used (config NOT overwritten)",
            )

        # clear parser
        self._timekprConfigParser.clear()

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finish loading configuration")

        # result
        return True

    def initDefaultConfiguration(self, pReuseValues=False):
        """Save config file (if someone messed up config file, we have to write new one)"""
        log.log(cons.TK_LOG_LEVEL_INFO, "start saving default configuration")

        # clear parser
        self._timekprConfigParser.clear()

        # save default config
        section = "DOCUMENTATION"
        self._timekprConfigParser.add_section(section)
        self._timekprConfigParser.set(
            section, "#### this is the main configuration file for timekpr-next"
        )
        self._timekprConfigParser.set(
            section,
            "#### if this file cannot be read properly, it will be overwritten with defaults",
        )

        section = "GENERAL"
        self._timekprConfigParser.add_section(section)
        self._timekprConfigParser.set(section, "#### general configuration section")
        # set up param
        param = "TIMEKPR_LOGLEVEL"
        self._timekprConfigParser.set(
            section,
            "# this defines logging level of the timekpr (1 - normal, 2 - debug, 3 - extra debug)",
        )
        self._timekprConfigParser.set(
            section,
            f"{param}",
            str(self._timekprConfig[param])
            if pReuseValues
            else str(cons.TK_LOG_LEVEL_INFO),
        )
        # set up param
        param = "TIMEKPR_POLLTIME"
        self._timekprConfigParser.set(
            section, "# this defines polling time (in memory) in seconds"
        )
        self._timekprConfigParser.set(
            section,
            f"{param}",
            str(self._timekprConfig[param]) if pReuseValues else str(cons.TK_POLLTIME),
        )
        # set up param
        param = "TIMEKPR_SAVE_TIME"
        self._timekprConfigParser.set(
            section,
            "# this defines a time for saving user time control file (polling and accounting is done in memory more often, but saving is not)",
        )
        self._timekprConfigParser.set(
            section,
            f"{param}",
            str(self._timekprConfig[param])
            if pReuseValues
            else str(cons.TK_SAVE_INTERVAL),
        )
        # set up param
        param = "TIMEKPR_TRACK_INACTIVE"
        self._timekprConfigParser.set(
            section,
            "# this defines whether to account sessions which are inactive (locked screen, user switched away from desktop, etc.),",
        )
        self._timekprConfigParser.set(
            section, "#   new users, when created, will inherit this value"
        )
        self._timekprConfigParser.set(
            section,
            f"{param}",
            str(self._timekprConfig[param])
            if pReuseValues
            else str(cons.TK_TRACK_INACTIVE),
        )
        # set up param
        param = "TIMEKPR_TERMINATION_TIME"
        self._timekprConfigParser.set(
            section,
            "# this defines a time interval in seconds prior to assign user a termination sequence",
        )
        self._timekprConfigParser.set(
            section,
            "#   15 seconds before time ends nothing can be done to avoid killing a session",
        )
        self._timekprConfigParser.set(
            section,
            "#   this also is the time before initiating a termination sequence if user has logged in inappropriate time",
        )
        self._timekprConfigParser.set(
            section,
            f"{param}",
            str(self._timekprConfig[param])
            if pReuseValues
            else str(cons.TK_TERMINATION_TIME),
        )
        # set up param
        param = "TIMEKPR_FINAL_WARNING_TIME"
        self._timekprConfigParser.set(
            section,
            "# this defines a time interval prior to termination of user sessions when timekpr will send continous final warnings (countdown) until the actual termination",
        )
        self._timekprConfigParser.set(
            section,
            f"{param}",
            str(self._timekprConfig[param])
            if pReuseValues
            else str(cons.TK_FINAL_COUNTDOWN_TIME),
        )
        # set up param
        param = "TIMEKPR_FINAL_NOTIFICATION_TIME"
        self._timekprConfigParser.set(
            section,
            "# this defines a time interval prior to termination of user sessions when timekpr will send one final warning about time left",
        )
        self._timekprConfigParser.set(
            section,
            f"{param}",
            str(self._timekprConfig[param])
            if pReuseValues
            else str(cons.TK_FINAL_NOTIFICATION_TIME),
        )

        section = "SESSION"
        self._timekprConfigParser.add_section(section)
        self._timekprConfigParser.set(
            section, "#### this section contains configuration about sessions"
        )
        # set up param
        param = "TIMEKPR_SESSION_TYPES_CTRL"
        self._timekprConfigParser.set(section, "# session types timekpr will track")
        self._timekprConfigParser.set(
            section,
            f"{param}",
            self._timekprConfig[param] if pReuseValues else cons.TK_SESSION_TYPES_CTRL,
        )
        # set up param
        param = "TIMEKPR_SESSION_TYPES_EXCL"
        self._timekprConfigParser.set(
            section, "# session types timekpr will ignore explicitly"
        )
        self._timekprConfigParser.set(
            section,
            f"{param}",
            self._timekprConfig[param] if pReuseValues else cons.TK_SESSION_TYPES_EXCL,
        )
        # set up param
        param = "TIMEKPR_USERS_EXCL"
        self._timekprConfigParser.set(section, "# users timekpr will ignore explicitly")
        self._timekprConfigParser.set(
            section,
            f"{param}",
            self._timekprConfig[param] if pReuseValues else cons.TK_USERS_EXCL,
        )

        section = "DIRECTORIES"
        self._timekprConfigParser.add_section(section)
        self._timekprConfigParser.set(
            section, "#### this section contains directory configuration"
        )
        # set up param
        param = "TIMEKPR_CONFIG_DIR"
        self._timekprConfigParser.set(
            section, "# runtime directory for timekpr user configuration files"
        )
        self._timekprConfigParser.set(
            section,
            f"{param}",
            self._timekprConfig[param] if pReuseValues else cons.TK_CONFIG_DIR,
        )
        # set up param
        param = "TIMEKPR_WORK_DIR"
        self._timekprConfigParser.set(
            section, "# runtime directory for timekpr time control files"
        )
        self._timekprConfigParser.set(
            section,
            f"{param}",
            self._timekprConfig[param] if pReuseValues else cons.TK_WORK_DIR,
        )
        # set up param
        param = "TIMEKPR_SHARED_DIR"
        self._timekprConfigParser.set(
            section, "# directory for shared files (images, gui definitions, etc.)"
        )
        self._timekprConfigParser.set(
            section,
            f"{param}",
            self._timekprConfig[param] if pReuseValues else cons.TK_SHARED_DIR,
        )
        # set up param
        param = "TIMEKPR_LOGFILE_DIR"
        self._timekprConfigParser.set(section, "# directory for log files")
        self._timekprConfigParser.set(
            section,
            f"{param}",
            self._timekprConfig[param] if pReuseValues else cons.TK_LOGFILE_DIR,
        )

        # save the file
        with open(self._configFile, "w") as fp:
            self._timekprConfigParser.write(fp)
        # clear parser
        self._timekprConfigParser.clear()

        log.log(cons.TK_LOG_LEVEL_INFO, "finish saving default configuration")

    def saveTimekprConfiguration(self):
        """Write new sections of the file"""
        log.log(cons.TK_LOG_LEVEL_DEBUG, "start saving timekpr configuration")

        # init dict
        values = {}

        # server loglevel
        param = "TIMEKPR_LOGLEVEL"
        values[param] = str(self._timekprConfig[param])
        # in-memory polling time
        param = "TIMEKPR_POLLTIME"
        values[param] = str(self._timekprConfig[param])
        # time interval to save user spent time
        param = "TIMEKPR_SAVE_TIME"
        values[param] = str(self._timekprConfig[param])
        # track inactive (default value)
        param = "TIMEKPR_TRACK_INACTIVE"
        values[param] = str(self._timekprConfig[param])
        # termination time (allowed login time when there is no time left before user is thrown out)
        param = "TIMEKPR_TERMINATION_TIME"
        values[param] = str(self._timekprConfig[param])
        # final warning time (countdown to 0 before terminating session)
        param = "TIMEKPR_FINAL_WARNING_TIME"
        values[param] = str(self._timekprConfig[param])
        # final notification time (final warning before terminating session)
        param = "TIMEKPR_FINAL_NOTIFICATION_TIME"
        values[param] = str(self._timekprConfig[param])
        # which session types to control
        param = "TIMEKPR_SESSION_TYPES_CTRL"
        values[param] = str(self._timekprConfig[param])
        # explicitly excludeds ession types (do not count time in these sessions)
        param = "TIMEKPR_SESSION_TYPES_EXCL"
        values[param] = str(self._timekprConfig[param])
        # which users to exclude from time accounting
        param = "TIMEKPR_USERS_EXCL"
        values[param] = str(self._timekprConfig[param])
        # ## pass placeholders for directories ##
        # config dir
        param = "TIMEKPR_CONFIG_DIR"
        values[param] = None
        # work dir
        param = "TIMEKPR_WORK_DIR"
        values[param] = None
        # shared dir
        param = "TIMEKPR_SHARED_DIR"
        values[param] = None
        # log dir
        param = "TIMEKPR_LOGFILE_DIR"
        values[param] = None

        # edit client config file (using alternate method because configparser looses comments in the process)
        _saveConfigFile(self._configFile, values)

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finish saving timekpr configuration")

    def logMainConfiguration(self):
        """Log main timekpr config file"""
        # log
        log.log(cons.TK_LOG_LEVEL_INFO, "main configuration:")

        try:
            # log
            param = "TIMEKPR_LOGLEVEL"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprConfig[param]!s}",
            )
            # log
            param = "TIMEKPR_POLLTIME"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprConfig[param]!s}",
            )
            # log
            param = "TIMEKPR_SAVE_TIME"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprConfig[param]!s}",
            )
            # log
            param = "TIMEKPR_TRACK_INACTIVE"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprConfig[param]!s}",
            )
            # log
            param = "TIMEKPR_TERMINATION_TIME"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprConfig[param]!s}",
            )
            # log
            param = "TIMEKPR_FINAL_WARNING_TIME"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprConfig[param]!s}",
            )
            # log
            param = "TIMEKPR_FINAL_NOTIFICATION_TIME"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprConfig[param]!s}",
            )

            # log
            param = "TIMEKPR_SESSION_TYPES_CTRL"
            self._timekprConfig[param] = _cleanupValue(self._timekprConfig[param])
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprConfig[param]!s}",
            )
            # log
            param = "TIMEKPR_SESSION_TYPES_EXCL"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprConfig[param]!s}",
            )
            # log
            param = "TIMEKPR_USERS_EXCL"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprConfig[param]!s}",
            )
        # fail
        except Exception:
            # log
            log.log(cons.TK_LOG_LEVEL_INFO, "  configuration log failed")

    def getTimekprVersion(self):
        """Get version"""
        # param
        param = "TIMEKPR_VERSION"
        # result
        return self._timekprConfig[param]

    def getTimekprLogLevel(self):
        """Get logging level"""
        # param
        param = "TIMEKPR_LOGLEVEL"
        # result
        return self._timekprConfig[param]

    def getTimekprPollTime(self):
        """Get polling time"""
        # param
        param = "TIMEKPR_POLLTIME"
        # result
        return self._timekprConfig[param]

    def getTimekprSaveTime(self):
        """Get save time"""
        # param
        param = "TIMEKPR_SAVE_TIME"
        # result
        return self._timekprConfig[param]

    def getTimekprTrackInactive(self):
        """Get tracking inactive"""
        # param
        param = "TIMEKPR_TRACK_INACTIVE"
        # result
        return self._timekprConfig[param]

    def getTimekprTerminationTime(self):
        """Get termination time"""
        # param
        param = "TIMEKPR_TERMINATION_TIME"
        # result
        return self._timekprConfig[param]

    def getTimekprFinalWarningTime(self):
        """Get final warning time"""
        # param
        param = "TIMEKPR_FINAL_WARNING_TIME"
        # result
        return self._timekprConfig[param]

    def getTimekprFinalNotificationTime(self):
        """Get final notification time"""
        # param
        param = "TIMEKPR_FINAL_NOTIFICATION_TIME"
        # result
        return self._timekprConfig[param]

    def getTimekprSessionsCtrl(self):
        """Get sessions to control"""
        # param
        param = "TIMEKPR_SESSION_TYPES_CTRL"
        # result
        return (
            [
                rVal.strip()
                for rVal in self._timekprConfig[param].split(";")
                if rVal != ""
            ]
            if param in self._timekprConfig
            else []
        )

    def getTimekprSessionsExcl(self):
        """Get sessions to exclude"""
        # param
        param = "TIMEKPR_SESSION_TYPES_EXCL"
        # result
        return (
            [
                rVal.strip()
                for rVal in self._timekprConfig[param].split(";")
                if rVal != ""
            ]
            if param in self._timekprConfig
            else []
        )

    def getTimekprUsersExcl(self):
        """Get sessions to exclude"""
        # param
        param = "TIMEKPR_USERS_EXCL"
        # result
        return (
            [
                rVal.strip()
                for rVal in self._timekprConfig[param].split(";")
                if rVal != ""
            ]
            if param in self._timekprConfig
            else []
        )

    def getTimekprConfigDir(self):
        """Get config dir"""
        # param
        param = "TIMEKPR_CONFIG_DIR"
        # result
        return (
            cons.TK_CONFIG_DIR_DEV if cons.TK_DEV_ACTIVE else self._timekprConfig[param]
        )

    def getTimekprWorkDir(self):
        """Get working dir"""
        # param
        param = "TIMEKPR_WORK_DIR"
        # result
        return (
            cons.TK_WORK_DIR_DEV if cons.TK_DEV_ACTIVE else self._timekprConfig[param]
        )

    def getTimekprSharedDir(self):
        """Get shared dir"""
        # param
        param = "TIMEKPR_SHARED_DIR"
        # result
        return (
            cons.TK_SHARED_DIR_DEV if cons.TK_DEV_ACTIVE else self._timekprConfig[param]
        )

    def getTimekprLogfileDir(self):
        """Get log file dir"""
        # param
        param = "TIMEKPR_LOGFILE_DIR"
        # result
        return (
            cons.TK_LOGFILE_DIR_DEV
            if cons.TK_DEV_ACTIVE
            else self._timekprConfig[param]
        )

    def getTimekprLastModified(self):
        """Get last file modification time"""
        # result
        return datetime.fromtimestamp(os.path.getmtime(self._configFile))

    def setTimekprLogLevel(self, pLogLevel):
        """Set logging level"""
        # result
        self._timekprConfig["TIMEKPR_LOGLEVEL"] = pLogLevel

    def setTimekprPollTime(self, pPollingTimeSecs):
        """Set polling time"""
        # result
        self._timekprConfig["TIMEKPR_POLLTIME"] = pPollingTimeSecs

    def setTimekprSaveTime(self, pSaveTimeSecs):
        """Set save time"""
        # result
        self._timekprConfig["TIMEKPR_SAVE_TIME"] = pSaveTimeSecs

    def setTimekprTrackInactive(self, pTrackInactiveDefault):
        """Get tracking inactive"""
        # result
        self._timekprConfig["TIMEKPR_TRACK_INACTIVE"] = pTrackInactiveDefault

    def setTimekprTerminationTime(self, pTerminationTimeSecs):
        """Set termination time"""
        # result
        self._timekprConfig["TIMEKPR_TERMINATION_TIME"] = pTerminationTimeSecs

    def setTimekprFinalWarningTime(self, pFinalWarningTimeSecs):
        """Set final warning time"""
        # result
        self._timekprConfig["TIMEKPR_FINAL_WARNING_TIME"] = pFinalWarningTimeSecs

    def setTimekprFinalNotificationTime(self, pFinalNotificationTimeSecs):
        """Set final warning time"""
        # result
        self._timekprConfig["TIMEKPR_FINAL_NOTIFICATION_TIME"] = (
            pFinalNotificationTimeSecs
        )

    def setTimekprSessionsCtrl(self, pSessionsCtrl):
        """Set sessions to control"""
        self._timekprConfig["TIMEKPR_SESSION_TYPES_CTRL"] = ";".join(pSessionsCtrl)

    def setTimekprSessionsExcl(self, pSessionsExcl):
        """Set sessions to exclude"""
        self._timekprConfig["TIMEKPR_SESSION_TYPES_EXCL"] = ";".join(pSessionsExcl)

    def setTimekprUsersExcl(self, pUsersExcl):
        """Set sessions to exclude"""
        self._timekprConfig["TIMEKPR_USERS_EXCL"] = ";".join(pUsersExcl)


def _parseList(pValue):
    """The items of a ";" separated value, stripped, without the empty ones"""
    return [rVal.strip() for rVal in pValue.split(";") if rVal.strip() != ""]


def _parseAllowedHours(pValue):
    """The hours of an ALLOWED_HOURS value: {hour: {start, end, unaccounted}}"""
    # this is the dict for hour config
    allowedHours = {}
    # minutes can be specified in brackets after hour
    for rHour in _parseList(pValue):
        # determine hour and minutes
        hour, sMin, eMin, uacc = findHourStartEndMinutes(rHour)
        # hour is correct
        if hour is not None:
            # get our dict done
            allowedHours[str(hour)] = {
                cons.TK_CTRL_SMIN: sMin,
                cons.TK_CTRL_EMIN: eMin,
                cons.TK_CTRL_UACC: uacc,
            }
    # result
    return allowedHours


class timekprUserConfig:
    """Class will contain and provide config related functionality"""

    def __init__(self, pDirectory, pUserName):
        """Initialize config"""

        log.log(
            cons.TK_LOG_LEVEL_INFO, f"init user ({pUserName}) configuration manager"
        )

        # initialize class variables
        #   a policy is a user's ("alice") or a group's ("@kids"); the group
        #   ones live in their own subdirectory, the section is the target
        self._configFile = self.getPolicyFile(pDirectory, pUserName)
        self._userName = pUserName
        self._isGroup = self.isGroupTarget(pUserName)
        self._timekprUserConfig = {}
        # whether the policy file exists (set when loading)
        self._present = False

        # parser
        self._timekprUserConfigParser = configparser.ConfigParser(allow_no_value=True)
        self._timekprUserConfigParser.optionxform = str

        log.log(cons.TK_LOG_LEVEL_INFO, "finish user configuration manager")

    def __del__(self):
        """De-initialize config"""
        log.log(cons.TK_LOG_LEVEL_INFO, "de-init user configuration manager")

    @staticmethod
    def isGroupTarget(pTarget):
        """Whether a policy target names a group ("@group") rather than a user"""
        return len(pTarget) > 1 and pTarget.startswith(cons.TK_GROUP_TARGET_PREFIX)

    @staticmethod
    def getPolicyFile(pDirectory, pTarget):
        """The policy file of a user or a group target, whether or not it exists"""
        # the daemon validates targets before they get here (policy.isValidTarget);
        # this is the backstop against a name that would leave the directory
        if os.sep in pTarget or "\0" in pTarget:
            raise ValueError(f"not a policy target: {pTarget!r}")
        if timekprUserConfig.isGroupTarget(pTarget):
            return os.path.join(
                pDirectory,
                cons.TK_GROUP_CONFIG_DIR,
                cons.TK_USER_CONFIG_FILE
                % (pTarget[len(cons.TK_GROUP_TARGET_PREFIX) :]),
            )
        return os.path.join(pDirectory, cons.TK_USER_CONFIG_FILE % (pTarget))

    def getPolicyTarget(self):
        """The user ("alice") or group ("@kids") this policy is for"""
        return self._userName

    def isGroupPolicy(self):
        """Whether this is a group's policy"""
        return self._isGroup

    def isPolicyPresent(self):
        """Whether the policy file exists (after loading)"""
        return self._present

    def loadUserConfiguration(self):
        """Read the policy file; True if there is one.  A missing (or
        unreadable, see _loadAndPrepareConfigFile) file means "no policy":
        the defaults are used in memory and nothing is written, policies are
        only ever created by administrators."""
        log.log(cons.TK_LOG_LEVEL_DEBUG, "start load user configuration")

        # user config section
        section = self._userName
        # try to load config file
        result = _loadAndPrepareConfigFile(
            self._timekprUserConfigParser, self._configFile
        )
        self._present = result
        # value read result
        resultValue = True
        # read the values (the defaults, when there is no file)
        if not result:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_DEBUG,
                f"no policy file ({self._configFile}), defaults apply",
            )

        # read
        param = "ALLOWED_HOURS"
        for i in range(1, 7 + 1):
            resultValue, self._timekprUserConfig[f"{param}_{i!s}"] = (
                _readAndNormalizeValue(
                    self._timekprUserConfigParser.get,
                    section,
                    (f"{param}_{i!s}"),
                    pDefaultValue=cons.TK_ALLOWED_HOURS,
                    pCheckValue=None,
                    pOverallSuccess=resultValue,
                )
            )
        # read
        param = "ALLOWED_WEEKDAYS"
        resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(
            self._timekprUserConfigParser.get,
            section,
            param,
            pDefaultValue=cons.TK_ALLOWED_WEEKDAYS,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        self._timekprUserConfig[param] = _cleanupValue(self._timekprUserConfig[param])
        # read
        param = "LIMITS_PER_WEEKDAYS"
        resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(
            self._timekprUserConfigParser.get,
            section,
            param,
            pDefaultValue=cons.TK_LIMITS_PER_WEEKDAYS,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        self._timekprUserConfig[param] = _cleanupValue(self._timekprUserConfig[param])
        # read
        param = "LIMIT_PER_WEEK"
        resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(
            self._timekprUserConfigParser.getint,
            section,
            param,
            pDefaultValue=cons.TK_LIMIT_PER_WEEK,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        # read
        param = "LIMIT_PER_MONTH"
        resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(
            self._timekprUserConfigParser.getint,
            section,
            param,
            pDefaultValue=cons.TK_LIMIT_PER_MONTH,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        # read
        param = "TRACK_INACTIVE"
        resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(
            self._timekprUserConfigParser.getboolean,
            section,
            param,
            pDefaultValue=cons.TK_TRACK_INACTIVE,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        # read
        param = "HIDE_TRAY_ICON"
        resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(
            self._timekprUserConfigParser.getboolean,
            section,
            param,
            pDefaultValue=cons.TK_HIDE_TRAY_ICON,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        # read (group policies only: the groups this one takes precedence over)
        param = "OVERRIDES"
        if self._isGroup:
            resultValue, self._timekprUserConfig[param] = _readAndNormalizeValue(
                self._timekprUserConfigParser.get,
                section,
                param,
                pDefaultValue="",
                pCheckValue=None,
                pOverallSuccess=resultValue,
            )
            self._timekprUserConfig[param] = _cleanupValue(
                self._timekprUserConfig[param]
            )
        else:
            self._timekprUserConfig[param] = ""
        # if we could not read some values, save what we could + defaults
        if result and not resultValue:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"WARNING: some values in user config file ({self._configFile}) could not be read or new configuration option was introduced, valid values and defaults are used / saved instead",
            )
            # init config with partial values read and save what we could
            self.initUserConfiguration(True)

        # clear parser
        self._timekprUserConfigParser.clear()

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finish load user configuration")

        # result
        return result

    def initUserConfiguration(self, pReuseValues=False):
        """Write new sections of the file"""
        log.log(
            cons.TK_LOG_LEVEL_INFO,
            f"init default user ({self._userName}) configuration",
        )

        # clear parser
        self._timekprUserConfigParser.clear()
        # group policies live in their own directory
        os.makedirs(os.path.dirname(self._configFile), exist_ok=True)

        # save default config
        section = "DOCUMENTATION"
        self._timekprUserConfigParser.add_section(section)
        self._timekprUserConfigParser.set(
            section,
            "#### this is the {} policy file for timekpr-next".format(
                "group" if self._isGroup else "user"
            ),
        )
        self._timekprUserConfigParser.set(
            section,
            "#### if this file cannot be read properly, it is set aside and no policy applies",
        )
        self._timekprUserConfigParser.set(
            section, "#### all numeric time values are specified in seconds"
        )
        self._timekprUserConfigParser.set(
            section,
            "#### days and hours should be configured as per ISO 8601 (i.e. Monday is the first day of week (1-7) and hours are in 24h format (0-23))",
        )

        # add new user section
        section = self._userName
        self._timekprUserConfigParser.add_section(section)
        self._timekprUserConfigParser.set(
            section,
            "# this defines which hours are allowed (remove or add hours to limit access), configure limits for start/end minutes for hour in brackets,",
        )
        self._timekprUserConfigParser.set(
            section,
            "#   optionally enter ! in front of hour to mark it non-accountable, for example !22[00-15]",
        )
        # set up param
        param = "ALLOWED_HOURS"
        # set hours for all days
        for i in range(1, 7 + 1):
            self._timekprUserConfigParser.set(
                section,
                f"{param}_{i!s}",
                self._timekprUserConfig[f"{param}_{i!s}"]
                if pReuseValues
                else cons.TK_ALLOWED_HOURS,
            )
        # set up param
        param = "ALLOWED_WEEKDAYS"
        self._timekprUserConfigParser.set(
            section,
            "# this defines which days of the week a user can use computer (remove or add days to limit access)",
        )
        self._timekprUserConfigParser.set(
            section,
            f"{param}",
            self._timekprUserConfig[param]
            if pReuseValues
            else cons.TK_ALLOWED_WEEKDAYS,
        )
        # set up param
        param = "LIMITS_PER_WEEKDAYS"
        self._timekprUserConfigParser.set(
            section,
            "# this defines allowed time in seconds per week day a user can use the computer (number of values must match the number of values for option ALLOWED_WEEKDAYS)",
        )
        self._timekprUserConfigParser.set(
            section,
            f"{param}",
            self._timekprUserConfig[param]
            if pReuseValues
            else cons.TK_LIMITS_PER_WEEKDAYS,
        )
        # set up param
        param = "LIMIT_PER_WEEK"
        self._timekprUserConfigParser.set(
            section,
            "# this defines allowed time per week in seconds (in addition to other limits)",
        )
        self._timekprUserConfigParser.set(
            section,
            f"{param}",
            str(self._timekprUserConfig[param])
            if pReuseValues
            else str(cons.TK_LIMIT_PER_WEEK),
        )
        # set up param
        param = "LIMIT_PER_MONTH"
        self._timekprUserConfigParser.set(
            section,
            "# this defines allowed time per month in seconds (in addition to other limits)",
        )
        self._timekprUserConfigParser.set(
            section,
            f"{param}",
            str(self._timekprUserConfig[param])
            if pReuseValues
            else str(cons.TK_LIMIT_PER_MONTH),
        )
        # set up param
        param = "TRACK_INACTIVE"
        self._timekprUserConfigParser.set(
            section,
            "# this defines whether to account sessions which are inactive (locked screen, user switched away from desktop, etc.)",
        )
        self._timekprUserConfigParser.set(
            section,
            f"{param}",
            str(self._timekprUserConfig[param])
            if pReuseValues
            else str(cons.TK_TRACK_INACTIVE),
        )
        # set up param
        param = "HIDE_TRAY_ICON"
        self._timekprUserConfigParser.set(
            section, "# this defines whether to show icon and notifications for user"
        )
        self._timekprUserConfigParser.set(
            section,
            f"{param}",
            str(self._timekprUserConfig[param])
            if pReuseValues
            else str(cons.TK_HIDE_TRAY_ICON),
        )
        # set up param (group policies only)
        if self._isGroup:
            param = "OVERRIDES"
            self._timekprUserConfigParser.set(
                section,
                "# this defines which other groups' policies this policy takes precedence over for users in both (names separated by ;),",
            )
            self._timekprUserConfigParser.set(
                section,
                "#   the policies of groups that are not overridden are merged, the most restrictive value of every setting wins",
            )
            self._timekprUserConfigParser.set(
                section,
                f"{param}",
                self._timekprUserConfig[param] if pReuseValues else "",
            )
        # save the file
        with open(self._configFile, "w") as fp:
            self._timekprUserConfigParser.write(fp)
        # the policy exists now
        self._present = True

        # clear parser
        self._timekprUserConfigParser.clear()

        log.log(cons.TK_LOG_LEVEL_INFO, "finish init default user configuration")

    def saveUserConfiguration(self):
        """Write new sections of the file"""
        log.log(
            cons.TK_LOG_LEVEL_DEBUG,
            f"start saving new user ({self._userName}) configuration",
        )

        # init dict
        values = {}

        # allowed weekdays
        param = "ALLOWED_WEEKDAYS"
        values[param] = self._timekprUserConfig[param]
        # allowed hours for every week day
        for rDay in range(1, 7 + 1):
            param = f"ALLOWED_HOURS_{rDay!s}"
            values[param] = self._timekprUserConfig[param]
        # limits per weekdays
        param = "LIMITS_PER_WEEKDAYS"
        values[param] = self._timekprUserConfig[param]
        # limits per week
        param = "LIMIT_PER_WEEK"
        values[param] = str(self._timekprUserConfig[param])
        # limits per month
        param = "LIMIT_PER_MONTH"
        values[param] = str(self._timekprUserConfig[param])
        # track inactive
        param = "TRACK_INACTIVE"
        values[param] = str(self._timekprUserConfig[param])
        # try icon
        param = "HIDE_TRAY_ICON"
        values[param] = str(self._timekprUserConfig[param])
        # overrides (only group policy files have the key, others ignore it)
        param = "OVERRIDES"
        values[param] = self._timekprUserConfig[param]
        # edit client config file (using alternate method because configparser looses comments in the process)
        _saveConfigFile(self._configFile, values)

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finish saving new user configuration")

    def logUserConfiguration(self):
        """Log user timekpr config file"""
        # log
        log.log(cons.TK_LOG_LEVEL_INFO, f'user "{self._userName}" configuration:')

        try:
            # log
            param = "ALLOWED_HOURS"
            for i in range(1, 7 + 1):
                paramN = f"{param}_{int(i)}"
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    f"  {paramN}={self._timekprUserConfig[paramN]!s}",
                )
            # log
            param = "ALLOWED_WEEKDAYS"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprUserConfig[param]!s}",
            )
            # log
            param = "LIMITS_PER_WEEKDAYS"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprUserConfig[param]!s}",
            )
            # log
            param = "LIMIT_PER_WEEK"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprUserConfig[param]!s}",
            )
            # log
            param = "LIMIT_PER_MONTH"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprUserConfig[param]!s}",
            )
            # log
            param = "TRACK_INACTIVE"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprUserConfig[param]!s}",
            )
            # log
            param = "HIDE_TRAY_ICON"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprUserConfig[param]!s}",
            )
        # fail
        except Exception:
            # log
            log.log(cons.TK_LOG_LEVEL_INFO, "  configuration log failed")

    def getUserAllowedHours(self, pDay):
        """Get allowed hours"""
        # result
        return _parseAllowedHours(self._timekprUserConfig[f"ALLOWED_HOURS_{pDay}"])

    def getUserAllowedWeekdays(self):
        """Get allowed week days"""
        # result
        return _parseList(self._timekprUserConfig["ALLOWED_WEEKDAYS"])

    def getUserLimitsPerWeekdays(self):
        """Get allowed limits per week day"""
        # result
        return [
            int(rVal)
            for rVal in _parseList(self._timekprUserConfig["LIMITS_PER_WEEKDAYS"])
        ]

    def getUserWeekLimit(self):
        """Get limit per week"""
        # result
        return self._timekprUserConfig["LIMIT_PER_WEEK"]

    def getUserMonthLimit(self):
        """Get limit per month"""
        # result
        return self._timekprUserConfig["LIMIT_PER_MONTH"]

    def getUserTrackInactive(self):
        """Get whether to track inactive sessions"""
        # result
        return self._timekprUserConfig["TRACK_INACTIVE"]

    def getUserHideTrayIcon(self):
        """Get whether to hide icon and notifications"""
        # result
        return self._timekprUserConfig["HIDE_TRAY_ICON"]

    def getUserOverrides(self):
        """Get the groups this (group) policy takes precedence over"""
        # param
        param = "OVERRIDES"
        # result
        return [
            rVal.strip()
            for rVal in self._timekprUserConfig[param].split(";")
            if rVal.strip() != ""
        ]

    def getUserConfigLastModified(self):
        """Get last file modification time for user (None without a file)"""
        # result
        try:
            return datetime.fromtimestamp(os.path.getmtime(self._configFile))
        except OSError:
            return None

    def isDefaultPolicy(self):
        """Whether the policy restricts nothing: every setting has its default
        value (such a file is a leftover of versions that created one per user)"""
        # compare the parsed values, not the strings
        return (
            all(
                self.getUserAllowedHours(str(rDay))
                == _parseAllowedHours(cons.TK_ALLOWED_HOURS)
                for rDay in range(1, 7 + 1)
            )
            and self.getUserAllowedWeekdays() == _parseList(cons.TK_ALLOWED_WEEKDAYS)
            and self.getUserLimitsPerWeekdays()
            == [int(rVal) for rVal in _parseList(cons.TK_LIMITS_PER_WEEKDAYS)]
            and self.getUserWeekLimit() == cons.TK_LIMIT_PER_WEEK
            and self.getUserMonthLimit() == cons.TK_LIMIT_PER_MONTH
            and self.getUserTrackInactive() == cons.TK_TRACK_INACTIVE
            and self.getUserHideTrayIcon() == cons.TK_HIDE_TRAY_ICON
        )

    def _getLimitsByDay(self):
        """The per-day limits keyed by day (they are stored positionally
        against the allowed days; a day without a limit has none)"""
        days = self.getUserAllowedWeekdays()
        limits = self.getUserLimitsPerWeekdays()
        return {
            rDay: (limits[rIdx] if rIdx < len(limits) else 0)
            for rIdx, rDay in enumerate(days)
        }

    def getUserLimitForDay(self, pDay):
        """The limit of one day (an ISO weekday as a string), 0 when the day
        is not allowed"""
        return self._getLimitsByDay().get(str(pDay), 0)

    def mergeMostRestrictive(self, pConfigs):
        """Replace the limit settings with the most restrictive merge of the
        given (loaded) policies: fewer days, shorter hour intervals, smaller
        limits, and idle time counted if any of them counts it.  The
        user-specific settings (the tray icon) keep their own value."""
        # days: only days every policy allows, with the smallest limit
        limitsByDay = [rConfig._getLimitsByDay() for rConfig in pConfigs]
        days = sorted(
            set.intersection(*[set(rLimits) for rLimits in limitsByDay]), key=int
        )
        self.setUserAllowedWeekdays(days)
        self.setUserLimitsPerWeekdays(
            [min(rLimits[rDay] for rLimits in limitsByDay) for rDay in days]
        )
        # hours: only hours every policy allows, over the common part of the
        # hour; an hour is unaccounted (free) only if it is free in all of them
        allowedHours = {}
        for rDay in range(1, 7 + 1):
            day = str(rDay)
            hoursByPolicy = [rConfig.getUserAllowedHours(day) for rConfig in pConfigs]
            hours = {}
            for rHour in set.intersection(*[set(rHours) for rHours in hoursByPolicy]):
                startMin = max(
                    rHours[rHour][cons.TK_CTRL_SMIN] for rHours in hoursByPolicy
                )
                endMin = min(
                    rHours[rHour][cons.TK_CTRL_EMIN] for rHours in hoursByPolicy
                )
                # nothing left of the hour
                if startMin >= endMin:
                    continue
                hours[rHour] = {
                    cons.TK_CTRL_SMIN: startMin,
                    cons.TK_CTRL_EMIN: endMin,
                    cons.TK_CTRL_UACC: all(
                        rHours[rHour][cons.TK_CTRL_UACC] for rHours in hoursByPolicy
                    ),
                }
            allowedHours[day] = hours
        self.setUserAllowedHours(allowedHours)
        # totals: the smallest
        self.setUserWeekLimit(min(rConfig.getUserWeekLimit() for rConfig in pConfigs))
        self.setUserMonthLimit(min(rConfig.getUserMonthLimit() for rConfig in pConfigs))
        # idle time counts if any policy counts it
        self.setUserTrackInactive(
            any(rConfig.getUserTrackInactive() for rConfig in pConfigs)
        )

    def deletePolicy(self):
        """Remove the policy file (and its backup); True if there was one"""
        existed = False
        for rFile in (self._configFile, self._configFile + cons.TK_BACK_EXT):
            try:
                os.remove(rFile)
                existed = existed or rFile == self._configFile
            except FileNotFoundError:
                pass
        self._present = False
        # result
        return existed

    def setUserAllowedHours(self, pAllowedHours):
        """Set allowed hours"""
        # go through all days given for modifications
        for rDay, rHours in pAllowedHours.items():
            # inital hours
            hours = []

            # go through all hours (in correct order)
            for rHour in range(23 + 1):
                # convert once
                hour = str(rHour)

                # do we have config for this hour
                if hour in rHours:
                    # is this hour unaccounted
                    unaccounted = "!" if rHours[hour][cons.TK_CTRL_UACC] else ""
                    # do we have proper minuten
                    minutes = (
                        (
                            f"[{int(rHours[hour][cons.TK_CTRL_SMIN])}-{int(rHours[hour][cons.TK_CTRL_EMIN])}]"
                        )
                        if (
                            rHours[hour][cons.TK_CTRL_SMIN] > 0
                            or rHours[hour][cons.TK_CTRL_EMIN] < 60
                        )
                        else ""
                    )
                    # build up this hour
                    hours.append(f"{unaccounted}{hour}{minutes}")

            # add this hour to allowable list
            self._timekprUserConfig[f"ALLOWED_HOURS_{rDay!s}"] = ";".join(hours)

    def setUserAllowedWeekdays(self, pAllowedWeekdays):
        """Set allowed week days"""
        # set up weekdays
        self._timekprUserConfig["ALLOWED_WEEKDAYS"] = ";".join(
            map(str, pAllowedWeekdays)
        )

    def setUserLimitsPerWeekdays(self, pLimits):
        """Set allowed limits per week day"""
        # set up limits for weekdays
        self._timekprUserConfig["LIMITS_PER_WEEKDAYS"] = ";".join(map(str, pLimits))

    def setUserWeekLimit(self, pWeekLimitSecs):
        """Set limit per week"""
        # result
        self._timekprUserConfig["LIMIT_PER_WEEK"] = int(pWeekLimitSecs)

    def setUserMonthLimit(self, pMonthLimitSecs):
        """Set limit per month"""
        # result
        self._timekprUserConfig["LIMIT_PER_MONTH"] = int(pMonthLimitSecs)

    def setUserTrackInactive(self, pTrackInactive):
        """Set whether to track inactive sessions"""
        # set track inactive
        self._timekprUserConfig["TRACK_INACTIVE"] = bool(pTrackInactive)

    def setUserHideTrayIcon(self, pHideTrayIcon):
        """Set whether to hide icon and notifications"""
        # result
        self._timekprUserConfig["HIDE_TRAY_ICON"] = bool(pHideTrayIcon)

    def setUserOverrides(self, pOverrides):
        """Set the groups this (group) policy takes precedence over"""
        # result
        self._timekprUserConfig["OVERRIDES"] = ";".join(
            rGroup.strip() for rGroup in pOverrides if rGroup.strip() != ""
        )


class timekprUserControl:
    """Class will provide time spent file management functionality"""

    def __init__(self, pDirectory, pUserName):
        """Initialize config"""

        log.log(cons.TK_LOG_LEVEL_INFO, f"init user ({pUserName}) control")

        # initialize class variables
        self._configFile = os.path.join(pDirectory, f"{pUserName}.time")
        self._userName = pUserName
        self._timekprUserControl = {}

        # parser
        self._timekprUserControlParser = configparser.ConfigParser(allow_no_value=True)
        self._timekprUserControlParser.optionxform = str

        log.log(cons.TK_LOG_LEVEL_INFO, "finish init user control")

    def __del__(self):
        """De-initialize config"""
        log.log(cons.TK_LOG_LEVEL_INFO, "de-init user control")

    def loadUserControl(self, pValidateOnly=False):
        """Read user control config file"""
        log.log(
            cons.TK_LOG_LEVEL_DEBUG,
            f"start loading user control ({self._userName})",
        )

        # directory section
        section = self._userName
        # try to load config file
        result = _loadAndPrepareConfigFile(
            self._timekprUserControlParser, self._configFile
        )
        # value read result
        resultValue = True

        # the counters are created when a user is first tracked; when only
        # checking (the administration tools), a missing file yields zeros
        # in memory and is not created
        if not result and not pValidateOnly:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"ERROR: could not parse the user control file ({self._configFile}) properly, will recreate",
            )
            # init config
            self.initUserControl()
            # re-read the file
            self._timekprUserControlParser.read(self._configFile)
            # the file is there now
            result = True

        # read
        param = "TIME_SPENT_BALANCE"
        resultValue, self._timekprUserControl[param] = _readAndNormalizeValue(
            self._timekprUserControlParser.getint,
            section,
            param,
            pDefaultValue=0,
            pCheckValue=cons.TK_LIMIT_PER_DAY,
            pOverallSuccess=resultValue,
        )
        # read
        param = "TIME_SPENT_DAY"
        resultValue, self._timekprUserControl[param] = _readAndNormalizeValue(
            self._timekprUserControlParser.getint,
            section,
            param,
            pDefaultValue=0,
            pCheckValue=cons.TK_LIMIT_PER_DAY,
            pOverallSuccess=resultValue,
        )
        # read
        param = "TIME_SPENT_WEEK"
        resultValue, self._timekprUserControl[param] = _readAndNormalizeValue(
            self._timekprUserControlParser.getint,
            section,
            param,
            pDefaultValue=0,
            pCheckValue=cons.TK_LIMIT_PER_WEEK,
            pOverallSuccess=resultValue,
        )
        # read
        param = "TIME_SPENT_MONTH"
        resultValue, self._timekprUserControl[param] = _readAndNormalizeValue(
            self._timekprUserControlParser.getint,
            section,
            param,
            pDefaultValue=0,
            pCheckValue=cons.TK_LIMIT_PER_MONTH,
            pOverallSuccess=resultValue,
        )
        # read
        param = "LAST_CHECKED"
        resultValue, self._timekprUserControl[param] = _readAndNormalizeValue(
            self._timekprUserControlParser.get,
            section,
            param,
            pDefaultValue=datetime.now().replace(microsecond=0),
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )

        # if we could not read some values, save what we could + defaults
        if result and not resultValue:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"WARNING: some values in user control file ({self._configFile}) could not be read or new configuration option was introduced, valid values and defaults are used / saved instead",
            )
            # save what we could
            self.initUserControl(True)

        # clear parser
        self._timekprUserControlParser.clear()

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finish loading user control")

        # result
        return result

    def initUserControl(self, pReuseValues=False):
        """Write new sections of the file"""
        log.log(cons.TK_LOG_LEVEL_INFO, f"start init user ({self._userName}) control")

        # clear parser
        self._timekprUserControlParser.clear()

        # add new user section
        section = self._userName
        self._timekprUserControlParser.add_section(section)
        self._timekprUserControlParser.set(
            section, "#### NOTE - all number values are stored in seconds"
        )
        # set up param
        param = "TIME_SPENT_BALANCE"
        self._timekprUserControlParser.set(
            section, "# total time balance spent for this day"
        )
        self._timekprUserControlParser.set(
            section,
            f"{param}",
            str(self._timekprUserControl[param]) if pReuseValues else "0",
        )
        # set up param
        param = "TIME_SPENT_DAY"
        self._timekprUserControlParser.set(section, "# total time spent for this day")
        self._timekprUserControlParser.set(
            section,
            f"{param}",
            str(self._timekprUserControl[param]) if pReuseValues else "0",
        )
        # set up param
        param = "TIME_SPENT_WEEK"
        self._timekprUserControlParser.set(section, "# total spent for this week")
        self._timekprUserControlParser.set(
            section,
            f"{param}",
            str(self._timekprUserControl[param]) if pReuseValues else "0",
        )
        # set up param
        param = "TIME_SPENT_MONTH"
        self._timekprUserControlParser.set(section, "# total spent for this month")
        self._timekprUserControlParser.set(
            section,
            f"{param}",
            str(self._timekprUserControl[param]) if pReuseValues else "0",
        )
        # set up param
        param = "LAST_CHECKED"
        self._timekprUserControlParser.set(section, "# last update time of the file")
        self._timekprUserControlParser.set(
            section,
            f"{param}",
            self._timekprUserControl[param].strftime(cons.TK_DATETIME_FORMAT)
            if pReuseValues
            else datetime.now()
            .replace(microsecond=0)
            .strftime(cons.TK_DATETIME_FORMAT),
        )

        # save the file
        with open(self._configFile, "w") as fp:
            self._timekprUserControlParser.write(fp)

        # clear parser
        self._timekprUserControlParser.clear()

        log.log(cons.TK_LOG_LEVEL_INFO, "finish init user control")

    def saveControl(self):
        """Save configuration"""
        log.log(cons.TK_LOG_LEVEL_INFO, f"start save user ({self._userName}) control")

        # init dict
        values = {}

        # spent day (including bonuses)
        param = "TIME_SPENT_BALANCE"
        values[param] = str(int(self._timekprUserControl[param]))
        # spent day
        param = "TIME_SPENT_DAY"
        values[param] = str(int(self._timekprUserControl[param]))
        # spent week
        param = "TIME_SPENT_WEEK"
        values[param] = str(int(self._timekprUserControl[param]))
        # spent month
        param = "TIME_SPENT_MONTH"
        values[param] = str(int(self._timekprUserControl[param]))
        # last checked
        param = "LAST_CHECKED"
        values[param] = self._timekprUserControl[param].strftime(
            cons.TK_DATETIME_FORMAT
        )
        # edit control file (using alternate method because configparser looses comments in the process)
        _saveConfigFile(self._configFile, values)

        log.log(cons.TK_LOG_LEVEL_INFO, "finish save user control")

    def logUserControl(self):
        """Log user control config file"""
        # log
        log.log(cons.TK_LOG_LEVEL_INFO, f'user "{self._userName}" control:')

        try:
            # log
            param = "TIME_SPENT_BALANCE"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprUserControl[param]!s}",
            )
            # log
            param = "TIME_SPENT_DAY"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprUserControl[param]!s}",
            )
            # log
            param = "TIME_SPENT_WEEK"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprUserControl[param]!s}",
            )
            # log
            param = "TIME_SPENT_MONTH"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprUserControl[param]!s}",
            )
            # log
            param = "LAST_CHECKED"
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"  {param}={self._timekprUserControl[param]!s}",
            )
        # fail
        except Exception:
            # log
            log.log(cons.TK_LOG_LEVEL_INFO, "  configuration log failed")

    def getUserDateComponentChanges(self, pCheckDate, pValidationDate=None):
        """Determine whether days / weeks / months changed since last change date in file or other date"""
        # date to validate against
        validationDate = (
            pValidationDate.date()
            if pValidationDate is not None
            else self.getUserLastChecked().date()
        )
        checkDate = pCheckDate.date()
        # ## validations ##
        # month changed
        monthChanged = (
            checkDate.year != validationDate.year
            or checkDate.month != validationDate.month
        )
        # week changed
        weekChanged = checkDate.isocalendar()[1] != validationDate.isocalendar()[1] or (
            checkDate.isocalendar()[1] == validationDate.isocalendar()[1]
            and abs((checkDate - validationDate).days) > 7
        )
        # day changed
        dayChanged = checkDate != validationDate

        # result (day / week / month)
        return dayChanged, weekChanged, monthChanged

    def getUserTimeSpentBalance(self):
        """Get time spent for day (including bonues)"""
        # result
        return self._timekprUserControl["TIME_SPENT_BALANCE"]

    def getUserTimeSpentDay(self):
        """Get time spent for day"""
        # result
        return self._timekprUserControl["TIME_SPENT_DAY"]

    def getUserTimeSpentWeek(self):
        """Get time spent for week"""
        # result
        return self._timekprUserControl["TIME_SPENT_WEEK"]

    def getUserTimeSpentMonth(self):
        """Get time spent for month"""
        # result
        return self._timekprUserControl["TIME_SPENT_MONTH"]

    def getUserLastChecked(self):
        """Get last check time for user"""
        # result
        return self._timekprUserControl["LAST_CHECKED"]

    def getUserControlLastModified(self):
        """Get last file modification time for user (None without a file)"""
        # result
        try:
            return datetime.fromtimestamp(os.path.getmtime(self._configFile))
        except OSError:
            return None

    def isControlPresent(self):
        """Whether the counters file exists"""
        return os.path.isfile(self._configFile)

    def setUserTimeSpentBalance(self, pTimeSpent):
        """Set time spent for day (including bonuses)"""
        # result
        self._timekprUserControl["TIME_SPENT_BALANCE"] = pTimeSpent

    def setUserTimeSpentDay(self, pTimeSpentDay):
        """Set time spent for day"""
        # result
        self._timekprUserControl["TIME_SPENT_DAY"] = pTimeSpentDay

    def setUserTimeSpentWeek(self, pTimeSpentWeek):
        """Set time spent for week"""
        # result
        self._timekprUserControl["TIME_SPENT_WEEK"] = pTimeSpentWeek

    def setUserTimeSpentMonth(self, pTimeSpentMonth):
        """Set time spent for month"""
        # result
        self._timekprUserControl["TIME_SPENT_MONTH"] = pTimeSpentMonth

    def setUserLastChecked(self, pEffectiveDatetime):
        """Set last check time for user"""
        # result
        self._timekprUserControl["LAST_CHECKED"] = pEffectiveDatetime


class timekprClientConfig:
    """Class will hold and provide config management for user"""

    def __init__(self):
        """Initialize config"""
        # config
        self._timekprClientConfig = {}
        # get home
        self._userHome = os.path.expanduser("~")

        # set up log file name
        self._timekprClientConfig["TIMEKPR_LOGFILE_DIR"] = cons.TK_LOG_TEMP_DIR

        log.log(
            cons.TK_LOG_LEVEL_INFO, "start initializing client configuration manager"
        )

        # in dev
        self._configDirPrefix = os.getcwd() if cons.TK_DEV_ACTIVE else ""

        # main config
        self._timekprClientConfig["TIMEKPR_MAIN_CONFIG_DIR"] = os.path.join(
            self._configDirPrefix,
            (
                cons.TK_MAIN_CONFIG_DIR_DEV
                if cons.TK_DEV_ACTIVE
                else cons.TK_MAIN_CONFIG_DIR
            ),
        )
        self._configMainFile = os.path.join(
            self._timekprClientConfig["TIMEKPR_MAIN_CONFIG_DIR"],
            cons.TK_MAIN_CONFIG_FILE,
        )

        # config
        self._configFile = os.path.join(
            self._userHome, ".config/timekpr", cons.TK_MAIN_CONFIG_FILE
        )

        # config parser
        self._timekprClientConfigParser = configparser.ConfigParser(allow_no_value=True)
        self._timekprClientConfigParser.optionxform = str

        log.log(
            cons.TK_LOG_LEVEL_INFO, "finish initializing client configuration manager"
        )

    def __del__(self):
        """De-initialize config"""
        log.log(cons.TK_LOG_LEVEL_INFO, "de-initialize client configuration manager")

    def loadClientConfiguration(self):
        """Read main timekpr config file"""
        log.log(cons.TK_LOG_LEVEL_DEBUG, "start loading client configuration")

        # get directories from main config
        if "TIMEKPR_SHARED_DIR" not in self._timekprClientConfig:
            # load main config to get directories
            self.loadMinimalClientMainConfig()
            # clear out cp, we don't need to store all condfigs in minimal case
            self._timekprClientConfigParser.clear()

        # try to load config file
        result = _loadAndPrepareConfigFile(
            self._timekprClientConfigParser, self._configFile
        )
        # value read result
        resultValue = True

        # read config failed, we need to initialize
        if not result:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"ERROR: could not parse the configuration file ({self._configFile}) properly, will use default values",
            )
            # write correct config file
            self.initClientConfig()
            # re-read the file
            self._timekprClientConfigParser.read(self._configFile)

        # config load time
        self._clientConfigModTime = self.getClientLastModified()

        # directory section
        section = "CONFIG"
        # read
        param = "LOG_LEVEL"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(
            self._timekprClientConfigParser.getint,
            section,
            param,
            pDefaultValue=cons.TK_LOG_LEVEL_INFO,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        # read
        param = "SHOW_LIMIT_NOTIFICATION"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(
            self._timekprClientConfigParser.getboolean,
            section,
            param,
            pDefaultValue=True,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        # read
        param = "SHOW_ALL_NOTIFICATIONS"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(
            self._timekprClientConfigParser.getboolean,
            section,
            param,
            pDefaultValue=True,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        # read
        param = "USE_SPEECH_NOTIFICATIONS"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(
            self._timekprClientConfigParser.getboolean,
            section,
            param,
            pDefaultValue=False,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        # read
        param = "SHOW_SECONDS"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(
            self._timekprClientConfigParser.getboolean,
            section,
            param,
            pDefaultValue=False,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        # read
        param = "NOTIFICATION_TIMEOUT"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(
            self._timekprClientConfigParser.getint,
            section,
            param,
            pDefaultValue=cons.TK_CL_NOTIF_TMO,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        # read
        param = "NOTIFICATION_TIMEOUT_CRITICAL"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(
            self._timekprClientConfigParser.getint,
            section,
            param,
            pDefaultValue=cons.TK_CL_NOTIF_CRIT_TMO,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        # read
        param = "USE_NOTIFICATION_SOUNDS"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(
            self._timekprClientConfigParser.getboolean,
            section,
            param,
            pDefaultValue=False,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        # read
        param = "NOTIFICATION_LEVELS"
        resultValue, self._timekprClientConfig[param] = _readAndNormalizeValue(
            self._timekprClientConfigParser.get,
            section,
            param,
            pDefaultValue=cons.TK_NOTIFICATION_LEVELS,
            pCheckValue=None,
            pOverallSuccess=resultValue,
        )
        self._timekprClientConfig[param] = _cleanupValue(
            self._timekprClientConfig[param]
        )
        # if we could not read some values, save what we could + defaults
        if not resultValue:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"WARNING: some values in client confguration file ({self._configFile}) could not be read or new configuration option was introduced, valid values and defaults are used / saved instead",
            )
            # save what we could
            self.initClientConfig(True)

        # check whether sound is supported
        if cons.TK_CL_NOTIF_SND_TYPE == "sound-name":
            self._timekprClientConfig["USE_NOTIFICATION_SOUNDS_SUPPORTED"] = True
        else:
            self._timekprClientConfig["USE_NOTIFICATION_SOUNDS_SUPPORTED"] = (
                os.path.isfile(cons.TK_CL_NOTIF_SND_FILE_WARN)
                and os.path.isfile(cons.TK_CL_NOTIF_SND_FILE_CRITICAL)
            )

        # check whether speech is supported
        try:
            # try importing speech
            from timekpr.client.interface.speech import espeak

            # supported
            self._timekprClientConfig["USE_SPEECH_NOTIFICATIONS_SUPPORTED"] = (
                espeak.isSupported()
            )
        except Exception:
            # NOT supported
            self._timekprClientConfig["USE_SPEECH_NOTIFICATIONS_SUPPORTED"] = False

        # clear parser
        self._timekprClientConfigParser.clear()

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finish loading client configuration")

        # result
        return True

    def loadMinimalClientMainConfig(self):
        """Load main configuration file to get shared file locations"""
        log.log(cons.TK_LOG_LEVEL_DEBUG, "start loading minimal main configuration")
        # defaults
        # directory section
        section = "DIRECTORIES"
        # read
        param = "TIMEKPR_SHARED_DIR"

        # try to load config file
        result = _loadAndPrepareConfigFile(
            self._timekprClientConfigParser, self._configMainFile, True
        )
        # if file cannot be read
        if not result:
            # default value
            value = cons.TK_SHARED_DIR
        else:
            # read file
            result, value = _readAndNormalizeValue(
                self._timekprClientConfigParser.get,
                section,
                param,
                pDefaultValue=cons.TK_SHARED_DIR,
                pCheckValue=None,
                pOverallSuccess=True,
            )

        # problems loading default config
        if not result:
            # logging
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"ERROR: could not parse the configuration file ({self._configMainFile}) properly, will use default values",
            )

        # finalize directory
        self._timekprClientConfig[param] = os.path.join(
            self._configDirPrefix,
            (cons.TK_SHARED_DIR_DEV if cons.TK_DEV_ACTIVE else value),
        )

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finish loading minimal main configuration")

        # result
        return True

    def initClientConfig(self, pReuseValues=False):
        """Write new config"""
        log.log(cons.TK_LOG_LEVEL_INFO, "start init client configuration")

        # directory name
        dirName = os.path.dirname(self._configFile)

        # check file
        if not os.path.isdir(dirName):
            # make it
            os.makedirs(dirName)

        # clear parser
        self._timekprClientConfigParser.clear()

        # add new user section
        section = "CONFIG"
        self._timekprClientConfigParser.add_section(section)
        self._timekprClientConfigParser.set(
            section, "# client application configuration file"
        )
        self._timekprClientConfigParser.set(
            section,
            "# NOTE - this file is not intended to be edited manually, however, if it is, please restart application",
        )
        self._timekprClientConfigParser.set(section, "")
        # set up param
        param = "LOG_LEVEL"
        self._timekprClientConfigParser.set(
            section, "# user logging level (1 - normal, 2 - debug, 3 - extra debug)"
        )
        self._timekprClientConfigParser.set(
            section,
            f"{param}",
            str(self._timekprClientConfig[param])
            if pReuseValues
            else str(cons.TK_LOG_LEVEL_INFO),
        )
        # set up param
        param = "SHOW_LIMIT_NOTIFICATION"
        self._timekprClientConfigParser.set(
            section, "# whether to show limit change notification"
        )
        self._timekprClientConfigParser.set(
            section,
            f"{param}",
            str(self._timekprClientConfig[param]) if pReuseValues else "True",
        )
        # set up param
        param = "SHOW_ALL_NOTIFICATIONS"
        self._timekprClientConfigParser.set(
            section, "# whether to show all notifications or important ones only"
        )
        self._timekprClientConfigParser.set(
            section,
            f"{param}",
            str(self._timekprClientConfig[param]) if pReuseValues else "True",
        )
        # set up param
        param = "SHOW_SECONDS"
        self._timekprClientConfigParser.set(
            section, "# whether to show seconds in label (if DE supports it)"
        )
        self._timekprClientConfigParser.set(
            section,
            f"{param}",
            str(self._timekprClientConfig[param]) if pReuseValues else "True",
        )
        # set up param
        param = "USE_SPEECH_NOTIFICATIONS"
        self._timekprClientConfigParser.set(
            section, "# whether to use speech notifications"
        )
        self._timekprClientConfigParser.set(
            section,
            f"{param}",
            str(self._timekprClientConfig[param])
            if pReuseValues
            else str(cons.TK_TRACK_INACTIVE),
        )
        # set up param
        param = "NOTIFICATION_TIMEOUT"
        self._timekprClientConfigParser.set(
            section, "# how long regular notifications should be displayed (in seconds)"
        )
        self._timekprClientConfigParser.set(
            section,
            f"{param}",
            str(self._timekprClientConfig[param])
            if pReuseValues
            else str(cons.TK_CL_NOTIF_TMO),
        )
        # set up param
        param = "NOTIFICATION_TIMEOUT_CRITICAL"
        self._timekprClientConfigParser.set(
            section,
            "# how long critical notifications should be displayed (in seconds)",
        )
        self._timekprClientConfigParser.set(
            section,
            f"{param}",
            str(self._timekprClientConfig[param])
            if pReuseValues
            else str(cons.TK_CL_NOTIF_CRIT_TMO),
        )
        # set up param
        param = "USE_NOTIFICATION_SOUNDS"
        self._timekprClientConfigParser.set(
            section, "# use notification sounds for notifications"
        )
        self._timekprClientConfigParser.set(
            section,
            f"{param}",
            str(self._timekprClientConfig[param])
            if pReuseValues
            else str(cons.TK_TRACK_INACTIVE),
        )
        # set up param
        param = "NOTIFICATION_LEVELS"
        self._timekprClientConfigParser.set(
            section,
            "# user configured notification levels in form of level[priority];...",
        )
        self._timekprClientConfigParser.set(
            section,
            f"{param}",
            self._timekprClientConfig[param]
            if pReuseValues
            else cons.TK_NOTIFICATION_LEVELS,
        )
        # save the file
        with open(self._configFile, "w") as fp:
            self._timekprClientConfigParser.write(fp)

        # clear parser
        self._timekprClientConfigParser.clear()

        log.log(cons.TK_LOG_LEVEL_INFO, "finish init client configuration")

    def saveClientConfig(self):
        """Save configuration (called from GUI)"""
        log.log(cons.TK_LOG_LEVEL_INFO, "start save client config")

        # init dict
        values = {}

        # log level
        param = "LOG_LEVEL"
        values[param] = str(self._timekprClientConfig[param])
        # first limit notification
        param = "SHOW_LIMIT_NOTIFICATION"
        values[param] = str(self._timekprClientConfig[param])
        # all notifications
        param = "SHOW_ALL_NOTIFICATIONS"
        values[param] = str(self._timekprClientConfig[param])
        # speech notifications
        param = "USE_SPEECH_NOTIFICATIONS"
        values[param] = str(self._timekprClientConfig[param])
        # show seconds
        param = "SHOW_SECONDS"
        values[param] = str(self._timekprClientConfig[param])
        # timeout for notifications
        param = "NOTIFICATION_TIMEOUT"
        values[param] = str(self._timekprClientConfig[param])
        # timeout for critical notifications
        param = "NOTIFICATION_TIMEOUT_CRITICAL"
        values[param] = str(self._timekprClientConfig[param])
        # notification sounds
        param = "USE_NOTIFICATION_SOUNDS"
        values[param] = str(self._timekprClientConfig[param])
        # notification levels
        param = "NOTIFICATION_LEVELS"
        values[param] = self._timekprClientConfig[param]

        # edit control file (using alternate method because configparser looses comments in the process)
        _saveConfigFile(self._configFile, values)

        log.log(cons.TK_LOG_LEVEL_INFO, "finish save client config")

    def isClientConfigChanged(self):
        """Whether config has changed"""
        # defaults
        result = False
        clientLastModified = self.getClientLastModified()

        # yes, is it changed?
        if self._clientConfigModTime != clientLastModified:
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"client config changed, prev/now: {self._clientConfigModTime.strftime(cons.TK_LOG_DATETIME_FORMAT)} / {clientLastModified.strftime(cons.TK_LOG_DATETIME_FORMAT)}",
            )
            # changed
            self._clientConfigModTime = clientLastModified
            # load config
            self.loadClientConfiguration()
            # changed
            result = True

        # result
        return result

    def _parseNotificationLevels(self, pKey):
        """Parse notification levels, if can not be parsed, return None"""
        # def
        result = []
        # work on levels
        for rLvl in self._timekprClientConfig[pKey].split(";"):
            # no need for non-empty values
            if rLvl != "":
                # try to find time left and level
                secs, prio = splitConfigValueNameParam(rLvl)
                # if identified correctly (e.g. we have secs and level too)
                # this is just to verify that config is OK
                if (
                    secs is not None
                    and prio is not None
                    and prio in cons.TK_PRIO_LVL_MAP
                ):
                    # add to list
                    result.append([int(secs), prio])
        # result
        return result

    def _formatClientNotificationLevels(self, pNotificationLevels):
        """Get formatted notification levels"""
        # def
        result = ""
        # loop through settings
        for rPrio in pNotificationLevels:
            # levels should be sorted from higher limit to lower
            result = "{}{}{}".format(
                result,
                ("" if result == "" else ";"),
                f"{rPrio[0]!s}[{rPrio[1]!s}]",
            )
        # result
        return result

    def getIsNotificationSoundSupported(self):
        """Whether notification sounds are supported"""
        # result
        return self._timekprClientConfig["USE_NOTIFICATION_SOUNDS_SUPPORTED"]

    def getIsNotificationSpeechSupported(self):
        """Whether speech notifications are supported"""
        # result
        return self._timekprClientConfig["USE_SPEECH_NOTIFICATIONS_SUPPORTED"]

    def getClientShowLimitNotifications(self):
        """Get whether to show frst notification"""
        # result
        return self._timekprClientConfig["SHOW_LIMIT_NOTIFICATION"]

    def getClientShowAllNotifications(self):
        """Get whether to show all notifications"""
        # result
        return self._timekprClientConfig["SHOW_ALL_NOTIFICATIONS"]

    def getClientUseSpeechNotifications(self):
        """Get whether to use speech"""
        # result
        return self._timekprClientConfig["USE_SPEECH_NOTIFICATIONS"]

    def getClientShowSeconds(self):
        """Get whether to show seconds"""
        # result
        return self._timekprClientConfig["SHOW_SECONDS"]

    def getClientNotificationTimeout(self):
        """Get timeout for regular notifications"""
        # result
        return self._timekprClientConfig["NOTIFICATION_TIMEOUT"]

    def getClientNotificationTimeoutCritical(self):
        """Get timeout for critical notifications"""
        # result
        return self._timekprClientConfig["NOTIFICATION_TIMEOUT_CRITICAL"]

    def getClientUseNotificationSound(self):
        """Get whether to show use sound notifications"""
        # result
        return self._timekprClientConfig["USE_NOTIFICATION_SOUNDS"]

    def getClientNotificationLevels(self):
        """Get notification levels"""
        # result
        return self._parseNotificationLevels("NOTIFICATION_LEVELS")

    def getClientLogLevel(self):
        """Get client log level"""
        # result
        return self._timekprClientConfig["LOG_LEVEL"]

    def getTimekprSharedDir(self):
        """Get shared dir"""
        # result
        return self._timekprClientConfig["TIMEKPR_SHARED_DIR"]

    def getClientLogfileDir(self):
        """Get shared dir"""
        # result
        return self._timekprClientConfig["TIMEKPR_LOGFILE_DIR"]

    def getClientLastModified(self):
        """Get last file modification time for user"""
        # result
        return datetime.fromtimestamp(os.path.getmtime(self._configFile))

    def setClientLogLevel(self, pClientLogLevel):
        """Set client log level"""
        # set
        self._timekprClientConfig["LOG_LEVEL"] = pClientLogLevel

    def setIsNotificationSoundSupported(self, pIsSupported):
        """Whether notification sounds are supported"""
        # result
        self._timekprClientConfig["USE_NOTIFICATION_SOUNDS_SUPPORTED"] = pIsSupported

    def setClientShowLimitNotifications(self, pClientShowLimitNotification):
        """Set whether to show frst notification"""
        # set
        self._timekprClientConfig["SHOW_LIMIT_NOTIFICATION"] = (
            pClientShowLimitNotification
        )

    def setClientShowAllNotifications(self, pClientShowAllNotifications):
        """Set whether to show all notifications"""
        # set
        self._timekprClientConfig["SHOW_ALL_NOTIFICATIONS"] = (
            pClientShowAllNotifications
        )

    def setClientUseSpeechNotifications(self, pClientUseSpeechNotifications):
        """Set whether to use speech"""
        # set
        self._timekprClientConfig["USE_SPEECH_NOTIFICATIONS"] = (
            pClientUseSpeechNotifications
        )

    def setClientShowSeconds(self, pClientShowSeconds):
        """Set whether to show seconds"""
        # set
        self._timekprClientConfig["SHOW_SECONDS"] = pClientShowSeconds

    def setClientNotificationTimeout(self, pClientNotificationTimeout):
        """Set timeout for regular notifications"""
        # set
        self._timekprClientConfig["NOTIFICATION_TIMEOUT"] = pClientNotificationTimeout

    def setClientNotificationTimeoutCritical(self, pClientNotificationTimeoutCritical):
        """Set timeout for critical notifications"""
        # set
        self._timekprClientConfig["NOTIFICATION_TIMEOUT_CRITICAL"] = (
            pClientNotificationTimeoutCritical
        )

    def setClientUseNotificationSound(self, pClientUseNotificationSound):
        """Set whether to use sound notifications"""
        # set
        self._timekprClientConfig["USE_NOTIFICATION_SOUNDS"] = (
            pClientUseNotificationSound
        )

    def setClientNotificationLevels(self, pNotificationLevels):
        """Set whether to use sound notifications"""
        # set
        self._timekprClientConfig["NOTIFICATION_LEVELS"] = (
            self._formatClientNotificationLevels(pNotificationLevels)
        )
