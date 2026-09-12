"""Run timekprw's main() on a fake daemon connector, for the listener tests.

With TIMEKPRW_TEST_ACTIVATION_FDS set to a comma separated list of file
descriptors, present them the way systemd socket activation does: as
descriptors 3, 4, ... with LISTEN_PID and LISTEN_FDS set."""

import os
import sys

from fake import FakeConnector
from timekpr.web import timekprw
from timekpr.web.bridge import Bridge

passed = os.environ.pop("TIMEKPRW_TEST_ACTIVATION_FDS", "")
if passed:
    for idx, fd in enumerate(passed.split(",")):
        os.dup2(int(fd), timekprw.SD_LISTEN_FDS_START + idx)
    os.environ["LISTEN_PID"] = str(os.getpid())
    os.environ["LISTEN_FDS"] = str(len(passed.split(",")))

timekprw.main(sys.argv[1:], bridge=Bridge(FakeConnector()))
