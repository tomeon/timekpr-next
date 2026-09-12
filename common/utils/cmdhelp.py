"""
Created on Sep 12, 2026

@author: mjasnik
"""

# ## This module is responsible for the command line help of timekpr's commands ##
# Help is answered before a command does anything else, so this module imports
# nothing at load time: whoever may execute a command must get its help, no
# matter whether the configuration, the log directory, a D-Bus session, a
# graphical environment or the daemon are there.  The texts need timekpr's
# constants and translations, those are imported when help is actually printed.

# the options which ask for help
TK_HELP_OPTIONS = ("-h", "--help")

# the commands which have help
TK_CMD_ADMIN = "timekpra"
TK_CMD_CLIENT = "timekprc"
TK_CMD_SERVER = "timekprd"

# usage of the commands which take no other options
_TK_USAGE = {
    TK_CMD_CLIENT: """Timekpr-nExT user client (v. %s)

Usage:
  timekprc            start the user client (tray icon and user notifications)
  timekprc -h|--help  print this help and exit

The client is normally started automatically when a user session starts.
Time limits are administered with timekpra.""",
    TK_CMD_SERVER: """Timekpr-nExT daemon (v. %s)

Usage:
  timekprd            start the timekpr daemon (needs superuser privileges)
  timekprd -h|--help  print this help and exit

The daemon is normally started by systemd (timekpr.service).
Time limits are administered with timekpra."""
}


def isHelpRequested(pArgs):
    """Check whether the arguments ask for help"""
    # this is all that may be done before help is printed
    return any(rArg in TK_HELP_OPTIONS for rArg in pArgs)


def printHelp(pCommand):
    """Print the help of the given command"""
    # timekpra has a command per line, the other two take no options at all
    if pCommand == TK_CMD_ADMIN:
        printAdminHelp()
    else:
        # imported here, so that asking for help loads nothing else
        from timekpr.common.constants import constants as cons
        from timekpr.common.log import log
        # print to console
        log.consoleOut(_TK_USAGE[pCommand] % (cons.TK_VERSION))


def printAdminHelp():
    """Print timekpra's usage notice and the commands it supports"""
    # imported here, so that asking for help loads nothing else
    from timekpr.common.constants import constants as cons
    from timekpr.common.constants import messages as msg
    from timekpr.common.log import log

    # log notice
    log.consoleOut("%s\n*) %s\n*) %s\n*) %s\n" % (
        msg.getTranslation("TK_MSG_CONSOLE_USAGE_NOTICE_HEAD"),
        msg.getTranslation("TK_MSG_CONSOLE_USAGE_NOTICE_TIME"),
        msg.getTranslation("TK_MSG_CONSOLE_USAGE_NOTICE_HOURS"),
        msg.getTranslation("TK_MSG_CONSOLE_USAGE_NOTICE_DAYS"))
    )
    # log usage notes text
    log.consoleOut("%s\n" % (msg.getTranslation("TK_MSG_CONSOLE_USAGE_NOTES")))
    # initial order
    cmds = ["--help", "--userlist", "--userinfo"]
    # print initial commands as first
    for rCmd in cmds:
        log.consoleOut(" ", rCmd, cons.TK_USER_ADMIN_COMMANDS[rCmd], "\n")

    # print help
    for rCmd, rCmdDesc in cons.TK_USER_ADMIN_COMMANDS.items():
        # do not print already known commands
        if rCmd not in cmds:
            log.consoleOut(" ", rCmd, rCmdDesc, "\n")
