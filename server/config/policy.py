"""
Effective policy resolution: which configuration applies to a user.

A policy is a configuration file an administrator created.  A user policy
is timekpr.<user>.conf in the configuration directory; a group policy is
groups/timekpr.<group>.conf, addressed as "@<group>" wherever a user name
is accepted.  Neither is created automatically.

For a user the effective policy is:

  1. the user's own policy, if the file exists (group policies are not
     consulted then);
  2. otherwise the most-restrictive merge of the policies of the groups
     the user belongs to (through NSS, os.getgrouplist), after every
     group that another matching group OVERRIDES, directly or
     transitively, has been dropped;  the pseudo-group "all" matches
     every user;
  3. otherwise the built-in defaults (no limits).

Group membership is asked from the user's side (os.getgrouplist), which
works for domain users whose identity provider does not enumerate.  A
group's member list is only ever needed for display and is best effort.
"""

import grp
import os
import pwd
import re
from glob import glob

from timekpr.common.constants import constants as cons
from timekpr.common.log import log
from timekpr.common.utils.config import timekprUserConfig

# a group is addressed as cons.TK_GROUP_TARGET_PREFIX + name ("@kids") where
# a user name is expected; a user name can never start with it (timekpr
# requires [a-zA-Z0-9_.] first, see userhelper), so the two never collide
# the pseudo-group every tracked user belongs to
TK_GROUP_ALL = "all"
# where the effective policy came from
TK_POLICY_SOURCE_USER = "user"
TK_POLICY_SOURCE_GROUP = "group"
TK_POLICY_SOURCE_DEFAULT = "default"


def isGroupTarget(pTarget):
    """Whether a target names a group (@group) rather than a user"""
    return isinstance(pTarget, str) and timekprUserConfig.isGroupTarget(pTarget)


def groupName(pTarget):
    """The group a target names (the target without its prefix)"""
    return (
        pTarget[len(cons.TK_GROUP_TARGET_PREFIX) :]
        if isGroupTarget(pTarget)
        else pTarget
    )


def groupTarget(pGroup):
    """The target naming a group"""
    return f"{cons.TK_GROUP_TARGET_PREFIX}{pGroup}"


def getUserGroups(pUserName):
    """The names of the groups a user belongs to, as NSS knows them (empty
    set for a user NSS does not know)"""
    groups = set()
    try:
        # the primary group is part of the answer
        gids = os.getgrouplist(pUserName, pwd.getpwnam(pUserName).pw_gid)
    except (KeyError, OSError):
        # not a user (any more)
        return groups
    # names, for the ones that have one
    for rGid in gids:
        try:
            groups.add(grp.getgrgid(rGid).gr_name)
        except KeyError:
            pass
    # result
    return groups


class timekprPolicyResolution:
    """What resolution produced for one user"""

    def __init__(self, pConfig, pSource, pGroups, pFingerprint):
        # the effective configuration (a timekprUserConfig, possibly merged)
        self.config = pConfig
        # user / group / default
        self.source = pSource
        # the groups that contributed (in merge order)
        self.groups = pGroups
        # a value that changes whenever a re-resolution could give another answer
        self.fingerprint = pFingerprint

    def getSourceDescription(self):
        """The provenance as one string, for logs and the admin tools"""
        if self.source == TK_POLICY_SOURCE_GROUP:
            return "{}:{}".format(self.source, ";".join(self.groups))
        return self.source


class timekprPolicyStore:
    """The policy files in a configuration directory and their resolution"""

    def __init__(self, pConfigDir):
        """Remember where the policies live"""
        self._configDir = pConfigDir

    # ## files ##

    def getConfigDir(self):
        """The configuration directory the policies live in"""
        return self._configDir

    def getUserPolicyFile(self, pUserName):
        """The path of a user's policy file (whether or not it exists)"""
        return timekprUserConfig.getPolicyFile(self._configDir, pUserName)

    def getGroupPolicyFile(self, pGroup):
        """The path of a group's policy file (whether or not it exists)"""
        return timekprUserConfig.getPolicyFile(self._configDir, groupTarget(pGroup))

    def hasUserPolicy(self, pUserName):
        return os.path.isfile(self.getUserPolicyFile(pUserName))

    def hasGroupPolicy(self, pGroup):
        return os.path.isfile(self.getGroupPolicyFile(pGroup))

    def getGroupsWithPolicy(self):
        """The groups that have a policy file, sorted"""
        groups = []
        pattern = cons.TK_USER_CONFIG_FILE.replace(".%s.", r"\.(.*)\.")
        for rFile in sorted(
            glob(
                os.path.join(
                    self._configDir,
                    cons.TK_GROUP_CONFIG_DIR,
                    cons.TK_USER_CONFIG_FILE % ("*"),
                )
            )
        ):
            # the sample file is not a policy
            if cons.TK_GROUP_CONFIG_SAMPLE in rFile:
                continue
            groups.append(re.sub(pattern, r"\1", os.path.basename(rFile)))
        # result
        return groups

    def getUsersWithPolicy(self):
        """The users that have a policy file, sorted"""
        users = []
        pattern = cons.TK_USER_CONFIG_FILE.replace(".%s.", r"\.(.*)\.")
        for rFile in sorted(
            glob(os.path.join(self._configDir, cons.TK_USER_CONFIG_FILE % ("*")))
        ):
            # the sample file is not a policy
            if cons.TK_USER_CONFIG_SAMPLE in rFile:
                continue
            users.append(re.sub(pattern, r"\1", os.path.basename(rFile)))
        # result
        return users

    # ## leftovers of automatically created user policies ##

    def getDefaultUserPolicies(self):
        """The users whose policy file restricts nothing (every value is a
        default): earlier versions created one for every user, and such a
        file now hides the group policies from its user"""
        users = []
        for rUser in self.getUsersWithPolicy():
            config = timekprUserConfig(self._configDir, rUser)
            if config.loadUserConfiguration() and config.isDefaultPolicy():
                users.append(rUser)
        # result
        return users

    def warnAboutDefaultPolicies(self):
        """Log a warning for every user policy that restricts nothing"""
        for rUser in self.getDefaultUserPolicies():
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f'WARNING: the policy of user "{rUser}" restricts nothing but keeps group policies from applying; delete it with "timekpra --deletepolicy" or "timekpra --migratepolicies"',
            )

    def migrateDefaultPolicies(self, pDryRun):
        """Delete every user policy that restricts nothing; return the users
        concerned (with pDryRun only list them)"""
        users = self.getDefaultUserPolicies()
        if not pDryRun:
            for rUser in users:
                timekprUserConfig(self._configDir, rUser).deletePolicy()
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    f'migration: deleted the policy of user "{rUser}", which restricted nothing',
                )
        # result
        return users

    # ## membership ##

    def getUserPolicyGroups(self, pUserName):
        """The groups with a policy that the user belongs to, sorted; the
        pseudo-group "all" whenever it has a policy"""
        withPolicy = set(self.getGroupsWithPolicy())
        groups = getUserGroups(pUserName) & withPolicy
        if TK_GROUP_ALL in withPolicy:
            groups.add(TK_GROUP_ALL)
        # result
        return sorted(groups)

    def getGroupMembers(self, pGroup, pKnownUsers=()):
        """Best-effort member list of a group: the explicit members NSS
        lists, plus every known user whose groups include it.  Identity
        providers that do not enumerate make this incomplete."""
        members = set()
        if pGroup == TK_GROUP_ALL:
            return sorted(pKnownUsers)
        try:
            members.update(grp.getgrnam(pGroup).gr_mem)
        except KeyError:
            pass
        for rUser in pKnownUsers:
            if pGroup in getUserGroups(rUser):
                members.add(rUser)
        # result
        return sorted(members)

    # ## resolution ##

    def _loadGroupPolicy(self, pGroup):
        """A group's policy as a loaded configuration object"""
        config = timekprUserConfig(self._configDir, groupTarget(pGroup))
        config.loadUserConfiguration()
        return config

    def _dropOverridden(self, pGroups, pConfigs):
        """Drop every group that another one of the groups overrides,
        directly or transitively.  A cycle is a configuration error: it is
        logged, and no group is dropped."""
        groups = set(pGroups)
        # the graph, restricted to the groups at hand
        edges = {
            rGroup: {
                rOther
                for rOther in pConfigs[rGroup].getUserOverrides()
                if rOther in groups and rOther != rGroup
            }
            for rGroup in groups
        }

        def _reach(pStart):
            seen = set()
            todo = list(edges[pStart])
            while todo:
                rNext = todo.pop()
                if rNext not in seen:
                    seen.add(rNext)
                    todo.extend(edges[rNext])
            return seen

        reach = {rGroup: _reach(rGroup) for rGroup in groups}
        cycle = [rGroup for rGroup in sorted(groups) if rGroup in reach[rGroup]]
        if cycle:
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                "ERROR: group policies override each other in a cycle ({}), precedence is ignored for them".format(
                    ", ".join(cycle)
                ),
            )
            return list(pGroups)
        overridden = set()
        for rGroup in groups:
            overridden.update(reach[rGroup])
        # result, in the given order
        return [rGroup for rGroup in pGroups if rGroup not in overridden]

    def resolve(self, pUserName):
        """The effective policy of a user, see the module documentation"""
        # the user's own policy wins outright
        config = timekprUserConfig(self._configDir, pUserName)
        present = config.loadUserConfiguration()
        userMtime = _mtime(self.getUserPolicyFile(pUserName))
        if present:
            return timekprPolicyResolution(
                config,
                TK_POLICY_SOURCE_USER,
                [],
                (userMtime, ()),
            )

        # the groups with a policy the user is in
        groups = self.getUserPolicyGroups(pUserName)
        configs = {rGroup: self._loadGroupPolicy(rGroup) for rGroup in groups}
        fingerprint = (
            userMtime,
            tuple(
                (rGroup, _mtime(self.getGroupPolicyFile(rGroup))) for rGroup in groups
            ),
        )
        applied = self._dropOverridden(groups, configs)
        if not applied:
            # defaults (config holds them, nothing was loaded)
            return timekprPolicyResolution(
                config, TK_POLICY_SOURCE_DEFAULT, [], fingerprint
            )

        # merge into the user's (default-valued) configuration object, so
        # that the rest of the daemon sees one ordinary configuration
        config.mergeMostRestrictive([configs[rGroup] for rGroup in applied])
        return timekprPolicyResolution(
            config, TK_POLICY_SOURCE_GROUP, applied, fingerprint
        )

    def fingerprint(self, pUserName):
        """The fingerprint resolve() would produce now, without loading
        the policies; cheap enough for a periodic check"""
        userMtime = _mtime(self.getUserPolicyFile(pUserName))
        if userMtime is not None:
            return (userMtime, ())
        groups = self.getUserPolicyGroups(pUserName)
        return (
            userMtime,
            tuple(
                (rGroup, _mtime(self.getGroupPolicyFile(rGroup))) for rGroup in groups
            ),
        )


def _mtime(pPath):
    """A file's modification time in nanoseconds, None if it does not exist"""
    try:
        return os.stat(pPath).st_mtime_ns
    except OSError:
        return None
