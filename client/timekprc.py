"""
Created on Aug 28, 2018

@author: mjasnik
"""
# imports
import os
import getpass
import sys
import signal
# set up our python path
if "/usr/lib/python3/dist-packages" not in sys.path:
    sys.path.append("/usr/lib/python3/dist-packages")

# timekpr imports
from timekpr.client.interface.dbus.daemon import timekprClient
from timekpr.common.constants import constants as cons
from timekpr.common.log import log
from timekpr.common.utils import misc

# usage text (the client itself takes no options)
_TK_USAGE = """Timekpr-nExT user client (v. %s)

Usage:
  timekprc         start the user client (tray icon and user notifications)
  timekprc --help  print this help and exit

The client is normally started automatically when a user session starts.
Time limits are administered with timekpra."""

# main start
if __name__ == "__main__":
    # help must work for anyone who can execute this, so it is printed before anything else is set up
    if misc.isHelpRequested(sys.argv[1:]):
        # print help and get out
        log.consoleOut(_TK_USAGE % (cons.TK_VERSION))
        sys.exit(0)

    # simple self-running check
    if misc.checkAndSetRunning(os.path.splitext(os.path.basename(__file__))[0], getpass.getuser()):
        # get out
        sys.exit(0)

    # get our client
    _timekprClient = timekprClient()

    # this is needed for appindicator to react to ctrl+c
    signal.signal(signal.SIGINT, _timekprClient.finishTimekpr)
    signal.signal(signal.SIGTERM, _timekprClient.finishTimekpr)

    # start up timekpr client
    _timekprClient.startTimekprClient()
