"""
Effective policy resolution: which configuration applies to a user.

A policy is a configuration file an administrator created.  A user policy
is timekpr.<user>.conf in the configuration directory; a group policy is
groups/timekpr.<group>.conf, addressed as "@<group>" wherever a user name
is accepted.  Neither is created automatically.

A policy file holds only the settings it sets.  For a user, every
setting of the effective policy is, in this order:

  1. the user's own value, if their policy sets the setting;
  2. otherwise the most-restrictive merge of the values of the group
     policies that set it, among the groups the user belongs to (through
     NSS, os.getgrouplist), after every group that another matching
     group OVERRIDES, directly or transitively, has been dropped;  the
     pseudo-group "all" matches every user;
  3. otherwise the built-in default (no limit).

The allowed days and their limits count as one setting, as do the hours
of one day; hiding the tray icon is the user's alone.

Group membership is asked from the user's side (os.getgrouplist), which
works for domain users whose identity provider does not enumerate.  A
group's member list is only ever needed for display and is best effort.

When NSS cannot answer (the identity provider is down, or a logged-in
user is no longer known), the membership is unknown, which is not the
same as no membership: resolve() and fingerprint() raise
timekprLookupError instead of answering, and the daemon keeps the
policy it last resolved (or, for a user it never resolved, applies
every group policy) until a lookup succeeds again.
"""

import grp
import os
import pwd
import re
from glob import glob

from timekpr.common.constants import constants as cons
from timekpr.common.log import log
from timekpr.common.utils.config import timekprUserConfig

# A group is addressed as cons.TK_GROUP_TARGET_PREFIX + name ("@kids")
# wherever a user name is expected; a user name can never start with the
# prefix (see _nameRegexp), so the two never collide.

# the pseudo-group every tracked user belongs to
TK_GROUP_ALL = "all"
# where the effective policy came from
TK_POLICY_SOURCE_USER = "user"
TK_POLICY_SOURCE_GROUP = "group"
TK_POLICY_SOURCE_DEFAULT = "default"
# the membership could not be looked up (see timekprLookupError)
TK_POLICY_SOURCE_UNRESOLVED = "unresolved"


class timekprLookupError(Exception):
    """NSS could not say who a user is or which groups they are in, so the
    effective policy cannot be resolved; callers keep what they had or
    fail closed, never fall back to the defaults.  unknownUser tells a
    name NSS does not know from a lookup that failed outright; for a
    logged-in user the first means the second."""

    def __init__(self, pMessage, pUnknownUser=False):
        super().__init__(pMessage)
        self.unknownUser = pUnknownUser


# the names timekpr accepts for a user or a group, at most 102 characters:
#   POSIX portable names, extended with uppercase characters, a leading digit
#   or ".", "@" for domain users and a trailing "$" for machine accounts;
#   nothing in it is path syntax, so a name is safe inside a file name
_nameRegexp = re.compile(
    r"^[a-zA-Z0-9_\.]([a-zA-Z0-9_\.@-]{0,101}|[a-zA-Z0-9_\.@-]{0,100}\$)$"
)


def isValidName(pName):
    """Whether a string is a user or group name timekpr accepts"""
    return isinstance(pName, str) and _nameRegexp.match(pName) is not None


def isValidTarget(pTarget):
    """Whether a policy target is a valid user name, or the prefix and a
    valid group name; everything the admin interfaces take a target from
    must pass here before the target reaches the file system"""
    return isinstance(pTarget, str) and isValidName(groupName(pTarget))


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


def userExists(pUserName):
    """Whether NSS knows the user (a directory user need not be enumerable,
    so this is asked by name, never from a listing)"""
    try:
        pwd.getpwnam(pUserName)
    except KeyError:
        return False
    return True


def getUserGroups(pUserName):
    """The names of the groups a user belongs to, as NSS knows them.  Raises
    timekprLookupError when NSS does not know the user or cannot answer
    (Python reports a directory that is down the same way as a name that
    does not exist, and neither is "no groups")."""
    groups = set()
    try:
        # the primary group is part of the answer
        gid = pwd.getpwnam(pUserName).pw_gid
    except KeyError as ex:
        raise timekprLookupError(
            f'user "{pUserName}" is not known to the system', pUnknownUser=True
        ) from ex
    except OSError as ex:
        raise timekprLookupError(
            f'user "{pUserName}" cannot be looked up: {ex}'
        ) from ex
    try:
        gids = os.getgrouplist(pUserName, gid)
    except (KeyError, OSError) as ex:
        raise timekprLookupError(
            f'the groups of user "{pUserName}" cannot be looked up: {ex}'
        ) from ex
    # names, for the ones that have one (a gid without a name is a stale
    # entry in the user's list, not a failed lookup)
    for rGid in gids:
        try:
            groups.add(grp.getgrgid(rGid).gr_name)
        except KeyError:
            log.log(
                cons.TK_LOG_LEVEL_DEBUG,
                f'user "{pUserName}" is in group {rGid}, which has no name',
            )
    # result
    return groups


class timekprPolicyResolution:
    """What resolution produced for one user"""

    def __init__(self, pConfig, pSource, pGroups, pFingerprint):
        # the effective configuration (a timekprUserConfig, every setting filled)
        self.config = pConfig
        # user (own settings, the rest from the groups) / group / default / unresolved
        self.source = pSource
        # the groups whose policies contributed (in merge order)
        self.groups = pGroups
        # a value that changes whenever a re-resolution could give another answer
        self.fingerprint = pFingerprint

    def getSourceDescription(self):
        """The provenance as one string, for logs and the admin tools: the
        source, and the groups that contribute after a colon"""
        if self.groups:
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
        """The users whose policy file is one an earlier version wrote for
        every user: it sets every setting, all of them to the defaults, so
        it restricts nothing but keeps the group policies from applying.
        (A policy that sets nothing is listed too: it does nothing.)"""
        users = []
        for rUser in self.getUsersWithPolicy():
            config = timekprUserConfig(self._configDir, rUser)
            try:
                if not config.loadUserConfiguration():
                    continue
                if config.getUnreadableParams():
                    # a value the administrator has to look at: not a leftover
                    log.log(
                        cons.TK_LOG_LEVEL_INFO,
                        f'WARNING: the policy of user "{rUser}" has values that cannot be read ({", ".join(config.getUnreadableParams())}); it is left alone',
                    )
                    continue
                if config.isLegacyPolicy() or config.isEmptyPolicy():
                    users.append(rUser)
            except Exception as ex:
                # the file is the administrator's to fix, it is neither
                # migrated nor allowed to stop the daemon
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    f'WARNING: the policy of user "{rUser}" ({self.getUserPolicyFile(rUser)}) cannot be read ({ex}); it is left alone',
                )
        # result
        return users

    def warnAboutDefaultPolicies(self):
        """Log a warning for every user policy that restricts nothing"""
        for rUser in self.getDefaultUserPolicies():
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f'WARNING: the policy of user "{rUser}" sets every setting to its default, so it restricts nothing but keeps the group policies from applying; delete it with "timekpra --deletepolicy" or "timekpra --migratepolicies"',
            )

    def migrateDefaultPolicies(self, pDryRun):
        """Delete every user policy that restricts nothing (see
        getDefaultUserPolicies); return the users concerned (with pDryRun
        only list them)"""
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
        pseudo-group "all" whenever it has a policy.  Raises
        timekprLookupError when NSS cannot answer."""
        withPolicy = set(self.getGroupsWithPolicy())
        groups = getUserGroups(pUserName) & withPolicy
        if TK_GROUP_ALL in withPolicy:
            groups.add(TK_GROUP_ALL)
        # result
        return sorted(groups)

    def startListing(self):
        """A listing of many users and groups at once (the admin lists), which
        asks NSS for a user's groups once and reads every group policy once"""
        return timekprPolicyListing(self)

    # ## resolution ##

    def _loadGroupPolicy(self, pGroup):
        """A group's policy as a loaded configuration object"""
        config = timekprUserConfig(self._configDir, groupTarget(pGroup))
        config.loadUserConfiguration()
        return config

    def _dropOverridden(self, pGroups, pOverrides):
        """Drop every group that another one of the groups overrides,
        directly or transitively (pOverrides gives each group's OVERRIDES
        list).  A cycle is a configuration error: it is logged, and no group
        is dropped."""
        groups = set(pGroups)
        # the graph, restricted to the groups at hand
        edges = {
            rGroup: {
                rOther
                for rOther in pOverrides(rGroup)
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
        """The effective policy of a user, see the module documentation.
        Raises timekprLookupError when NSS cannot say which groups the user
        is in (their own policy alone does not settle the settings it does
        not set)."""
        # the user's own policy (the modification time is read before the
        # file, so that an edit landing in between makes the next
        # fingerprint check resolve again, never the other way round)
        own = timekprUserConfig(self._configDir, pUserName)
        userMtime = _mtime(self.getUserPolicyFile(pUserName))
        present = own.loadUserConfiguration()
        if not present:
            # an unreadable file was set aside, an empty one removed
            userMtime = _mtime(self.getUserPolicyFile(pUserName))

        # the groups with a policy the user is in (modification times before
        # the files are read, as above)
        groups = self.getUserPolicyGroups(pUserName)
        fingerprint = (
            userMtime,
            tuple(
                (rGroup, _mtime(self.getGroupPolicyFile(rGroup))) for rGroup in groups
            ),
        )
        # result
        return self._resolveLayers(
            pUserName, own if present else None, groups, fingerprint, False
        )

    def resolveUnknownMembership(self, pUserName):
        """The policy for a user whose groups cannot be looked up and who was
        never resolved before: their own settings over the most restrictive
        merge of every group policy, since the user may be in any of the
        groups.  A directory that is down thus never lifts a restriction.
        The resolution has no fingerprint, so the next successful lookup
        replaces it."""
        own = timekprUserConfig(self._configDir, pUserName)
        present = own.loadUserConfiguration()
        # result
        return self._resolveLayers(
            pUserName, own if present else None, self.getGroupsWithPolicy(), None, True
        )

    def _resolveLayers(self, pUserName, pOwn, pGroups, pFingerprint, pUnresolved):
        """Layer the user's own policy (None without one) over the given
        groups' policies into one configuration object with every setting
        filled, so that the rest of the daemon sees one ordinary
        configuration"""
        configs = {rGroup: self._loadGroupPolicy(rGroup) for rGroup in pGroups}
        applied = self._dropOverridden(
            pGroups, lambda pGroup: configs[pGroup].getUserOverrides()
        )
        effective = timekprUserConfig(self._configDir, pUserName)
        effective.resolveLayers(pOwn, [configs[rGroup] for rGroup in applied])
        if pUnresolved:
            source = TK_POLICY_SOURCE_UNRESOLVED
        elif pOwn is not None:
            source = TK_POLICY_SOURCE_USER
        elif applied:
            source = TK_POLICY_SOURCE_GROUP
        else:
            source = TK_POLICY_SOURCE_DEFAULT
        # result
        return timekprPolicyResolution(effective, source, applied, pFingerprint)

    def fingerprint(self, pUserName):
        """The fingerprint resolve() would produce now, without loading
        the policies; cheap enough for a periodic check.  Raises
        timekprLookupError as resolve() does."""
        userMtime = _mtime(self.getUserPolicyFile(pUserName))
        groups = self.getUserPolicyGroups(pUserName)
        return (
            userMtime,
            tuple(
                (rGroup, _mtime(self.getGroupPolicyFile(rGroup))) for rGroup in groups
            ),
        )


class timekprPolicyListing:
    """One pass over many users and groups for the admin lists: a user's
    groups are asked from NSS once, and a group policy is read once, however
    many users it is consulted for.  Where a user's policy comes from is
    decided as timekprPolicyStore.resolve does, except that a user's policy
    file counts by its presence (an unreadable one is set aside when it is
    next read for enforcement)."""

    def __init__(self, pStore):
        self._store = pStore
        # the groups with a policy, once
        self._groupsWithPolicy = pStore.getGroupsWithPolicy()
        # the loaded group policies, on demand
        self._groupConfigs = {}
        # the groups of every user asked about so far
        self._userGroups = {}

    def getGroupsWithPolicy(self):
        """The groups that have a policy file, sorted"""
        return list(self._groupsWithPolicy)

    def getGroupConfig(self, pGroup):
        """A group's loaded policy (read once)"""
        if pGroup not in self._groupConfigs:
            self._groupConfigs[pGroup] = self._store._loadGroupPolicy(pGroup)
        return self._groupConfigs[pGroup]

    def getUserGroups(self, pUserName):
        """The names of a user's groups, as NSS knows them (asked once);
        None when NSS could not answer"""
        if pUserName not in self._userGroups:
            try:
                self._userGroups[pUserName] = getUserGroups(pUserName)
            except timekprLookupError as ex:
                log.log(cons.TK_LOG_LEVEL_DEBUG, f"listing: {ex}")
                self._userGroups[pUserName] = None
        return self._userGroups[pUserName]

    def getGroupMembers(self, pGroup, pKnownUsers=()):
        """Best-effort member list of a group: the explicit members NSS
        lists, plus every known user whose groups include it.  Identity
        providers that do not enumerate make this incomplete."""
        if pGroup == TK_GROUP_ALL:
            return sorted(pKnownUsers)
        members = set()
        try:
            members.update(grp.getgrnam(pGroup).gr_mem)
        except KeyError:
            pass
        for rUser in pKnownUsers:
            if pGroup in (self.getUserGroups(rUser) or ()):
                members.add(rUser)
        # result
        return sorted(members)

    def getSourceDescription(self, pUserName):
        """Where a user's effective policy comes from, as one string (see
        timekprPolicyResolution.getSourceDescription)"""
        # the groups with a policy the user is in
        userGroups = self.getUserGroups(pUserName)
        if userGroups is None:
            # NSS could not say (a name nobody knows, or a directory that is
            # down): not the defaults, whatever the daemon applies meanwhile
            return TK_POLICY_SOURCE_UNRESOLVED
        withPolicy = set(self._groupsWithPolicy)
        groups = userGroups & withPolicy
        if TK_GROUP_ALL in withPolicy:
            groups.add(TK_GROUP_ALL)
        applied = self._store._dropOverridden(
            sorted(groups),
            lambda pGroup: self.getGroupConfig(pGroup).getUserOverrides(),
        )
        if self._store.hasUserPolicy(pUserName):
            source = TK_POLICY_SOURCE_USER
        elif applied:
            source = TK_POLICY_SOURCE_GROUP
        else:
            return TK_POLICY_SOURCE_DEFAULT
        # result
        return "{}:{}".format(source, ";".join(applied)) if applied else source


def _mtime(pPath):
    """A file's modification time in nanoseconds, None if it does not exist"""
    try:
        return os.stat(pPath).st_mtime_ns
    except OSError:
        return None
