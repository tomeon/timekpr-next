"""
Created on Feb 05, 2019

@author: mjasnik
"""

# imports
import fileinput
import os
import pwd
import re

# timekpr imports
from timekpr.common.constants import constants as cons
from timekpr.common.log import log
from timekpr.common.utils.config import timekprConfig, timekprUserConfig
from timekpr.common.utils.misc import getNormalizedUserNames
from timekpr.server.config.policy import groupTarget, timekprPolicyStore

# user limits
_limitsConfig = {}
_loginManagers = [
    result.strip(None) for result in cons.TK_USERS_LOGIN_MANAGERS.split(";")
]

# defaults
_limitsConfig["UID_MIN"] = 1000
_limitsConfig["UID_MAX"] = 60000
# username pattern:
#   all users, max 101 chars
#   linux users, extended with uppercase characters and first numeric or "." character
#   domain users, extended with uppercase characters and first numeric or ".", and "@" symbol
_userNameRegexp = re.compile(
    r"^[a-zA-Z0-9_\.]([a-zA-Z0-9_\.@-]{0,101}|[a-zA-Z0-9_\.@-]{0,100}\$)$"
)


# some distros are "different", login.defs may be in different dir, config reflects multiple dirs to check for the file
for rFile in cons.TK_USER_LIMITS_FILE:
    # check if file exists
    if os.path.isfile(rFile):
        # load limits
        with fileinput.input(rFile) as rLimitsFile:
            # read line and do manipulations
            for rLine in rLimitsFile:
                # get min/max uids
                if re.match("^UID_M(IN|AX)[ \t]+[0-9]+$", rLine):
                    # find our config
                    x = re.findall(r"^([A-Z_]+)[ \t]+([0-9]+).*$", rLine)
                    # save min/max uuids
                    _limitsConfig[x[0][0]] = int(x[0][1])
            # fin
            break


def isUserValid(pUserId, pUserName=None, pUserShell=None):
    """Validate user ID, name and shell"""
    # vars
    isUIDOK = False

    # check user id
    if pUserId is not None and pUserId != "":
        # check normal users and to test in VMs default user (it may have UID of 999, -1 from limit)
        isUIDOK = int(pUserId) >= _limitsConfig["UID_MIN"] - 1
        # check shell (if provided)
        # uid is ok and shell is passed
        if (
            isUIDOK
            and pUserShell is not None
            and ("/nologin" in pUserShell or "/false" in pUserShell or "" == pUserShell)
        ):
            # user is not ours
            isUIDOK = False
        # check if username is ok
        # uid is ok and name is passed
        if isUIDOK and pUserName is not None and not _userNameRegexp.match(pUserName):
            # user is not ours
            isUIDOK = False
    # fin
    return isUIDOK


def getTimekprLoginManagers():
    """Get login manager names"""
    return _loginManagers


class timekprUserStore:
    """Class will privide methods to help managing users, like intialize the config for them"""

    def __init__(self):
        """Initialize timekprsystemusers"""
        log.log(cons.TK_LOG_LEVEL_DEBUG, "initializing timekprUserStore")

    def __del__(self):
        """Deinitialize timekprsystemusers"""
        log.log(cons.TK_LOG_LEVEL_DEBUG, "de-initializing timekprUserStore")

    def checkAndInitUsers(self):
        """List the users present in the system that timekpr would track.
        Nothing is created for them: a policy exists only when an
        administrator makes one, and the counters are created when a
        user is first tracked."""
        # config
        users = {}

        # iterate through all usernames
        for rUser in pwd.getpwall():
            # save our user, if it mactches
            if isUserValid(rUser.pw_uid, rUser.pw_name, rUser.pw_shell):
                # get processed usernames
                userFName = getNormalizedUserNames(pUser=rUser)[1]
                # save ()
                users[rUser.pw_name] = [rUser.pw_uid, userFName]

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finishing listing users")

        # user list
        return users

    def _getConfigDir(self, pConfigDir):
        """The configuration directory, from the main configuration if not given"""
        # in case we don't have a dir yet
        if pConfigDir is None:
            # get user config
            timekprConfigManager = timekprConfig()
            # load user config
            timekprConfigManager.loadMainConfiguration()
            # config dir
            return timekprConfigManager.getTimekprConfigDir()
        # use passed value
        return pConfigDir

    def getSavedUserList(self, pConfigDir=None):
        """
        Get the user list: the users with a policy file (which may name
        users that do not exist any more), the users present in the
        system, and the known members of the groups with a policy.  Every
        entry is [user, full name, where the effective policy comes from].
        """
        # initialize username storage
        userList = []

        # the users in the system
        users = self.checkAndInitUsers()
        # the policies
        policyStore = timekprPolicyStore(self._getConfigDir(pConfigDir))

        log.log(cons.TK_LOG_LEVEL_DEBUG, "listing user policy files")

        # the users with a policy of their own
        userNames = set()
        for rUser in policyStore.getUsersWithPolicy():
            # whether user is valid in config file
            userNameValidated = False
            # try to read the first line with username
            with open(policyStore.getUserPolicyFile(rUser), "r") as confFile:
                # read first (x) lines and try to get username
                for _i in range(cons.TK_UNAME_SRCH_LN_LMT):
                    # check whether we have correct username
                    if f"[{rUser}]" in confFile.readline():
                        # user validated
                        userNameValidated = True
                        # found
                        break
            # keep the validated ones
            if userNameValidated:
                userNames.add(rUser)
        # the users in the system
        userNames.update(users)
        # the known members of the groups with a policy (best effort)
        for rGroup in policyStore.getGroupsWithPolicy():
            userNames.update(policyStore.getGroupMembers(rGroup, users))

        log.log(cons.TK_LOG_LEVEL_DEBUG, "resolving user policies")

        # now walk the list
        for rUser in sorted(userNames):
            # user name, full name, and where the policy comes from
            userList.append(
                [
                    rUser,
                    users[rUser][1] if rUser in users else "",
                    policyStore.resolve(rUser).getSourceDescription(),
                ]
            )

        log.log(cons.TK_LOG_LEVEL_DEBUG, "finishing user list")

        # finish
        return userList

    def getSavedGroupList(self, pConfigDir=None):
        """
        Get the list of groups with a policy: every entry is [group, the
        groups it overrides (; separated), its known members (; separated,
        best effort)].
        """
        # the users in the system
        users = self.checkAndInitUsers()
        # the policies
        policyStore = timekprPolicyStore(self._getConfigDir(pConfigDir))
        # the list
        groupList = []
        for rGroup in policyStore.getGroupsWithPolicy():
            config = timekprUserConfig(policyStore.getConfigDir(), groupTarget(rGroup))
            config.loadUserConfiguration()
            groupList.append(
                [
                    rGroup,
                    ";".join(config.getUserOverrides()),
                    ";".join(policyStore.getGroupMembers(rGroup, users)),
                ]
            )
        # finish
        return groupList
