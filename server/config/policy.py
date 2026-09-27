"""
Effective policy resolution: which configuration applies to a user.

A policy is a set of settings an administrator made for a user, or for
a group, addressed as "@<group>" wherever a user name is accepted.
Neither is created automatically.  The policies are the rows of one
SQLite database in the configuration directory (TK_POLICY_DB_FILE, see
timekprPolicyStore): every change is one transaction, so concurrent
changes are serialized and a reader never sees half of one.  The policy
files of earlier versions are imported into it when the daemon starts.

A policy holds only the settings it sets.  For a user, every setting of
the effective policy is, in this order:

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

import contextlib
import grp
import os
import pwd
import re
import sqlite3
import threading
from glob import glob

from timekpr.common.constants import constants as cons
from timekpr.common.log import log
from timekpr.common.utils.config import (
    USER_CONFIG_DEFAULTS,
    USER_CONFIG_PARAMS,
    readBool,
    readPolicyFile,
    timekprUserConfig,
)

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


# ## the database ##

# the version of the schema below (PRAGMA user_version)
_SCHEMA_VERSION = 1
# the SQL type of each setting's column (named after the setting, in lower
# case); NULL is an unset setting
_COLUMN_TYPES = {
    **{f"ALLOWED_HOURS_{rDay}": "TEXT" for rDay in range(1, 7 + 1)},
    "ALLOWED_WEEKDAYS": "TEXT",
    "LIMITS_PER_WEEKDAYS": "TEXT",
    "LIMIT_PER_WEEK": "INTEGER",
    "LIMIT_PER_MONTH": "INTEGER",
    "TRACK_INACTIVE": "BOOLEAN",
    "HIDE_TRAY_ICON": "BOOLEAN",
    "OVERRIDES": "TEXT",
}
_COLUMNS = [rParam.lower() for rParam in USER_CONFIG_PARAMS]


def _schema():
    """The policy table: one row per policy, "alice" for a user and "@kids"
    for a group, a column per setting.  The constraints are the invariants
    the daemon keeps, so that no writer can break them: the value types,
    the allowed days and their limits set together, overrides only in a
    group policy and the tray icon only in a user's."""
    columns = ["target TEXT PRIMARY KEY NOT NULL CHECK (length(target) > 0)"]
    for rParam, rColumn in zip(USER_CONFIG_PARAMS, _COLUMNS):
        kind = _COLUMN_TYPES[rParam]
        check = (
            f"{rColumn} IN (0, 1)"
            if kind == "BOOLEAN"
            else f"typeof({rColumn}) = '{kind.lower()}'"
        )
        sqlType = "INTEGER" if kind == "BOOLEAN" else kind
        columns.append(f"{rColumn} {sqlType} CHECK ({rColumn} IS NULL OR {check})")
    group = f"substr(target, 1, 1) = '{cons.TK_GROUP_TARGET_PREFIX}'"
    columns += [
        "CHECK ((allowed_weekdays IS NULL) = (limits_per_weekdays IS NULL))",
        f"CHECK (overrides IS NULL OR {group})",
        f"CHECK (hide_tray_icon IS NULL OR NOT {group})",
    ]
    return "CREATE TABLE policy (\n    {}\n)".format(",\n    ".join(columns))


def _connect(pDbFile):
    """A connection to the policy database, created with its schema if
    there is none yet.  Statements run outside a transaction unless one is
    begun explicitly (isolation_level None): the store begins every one
    itself."""
    os.makedirs(os.path.dirname(pDbFile), exist_ok=True)
    conn = sqlite3.connect(
        pDbFile, timeout=cons.TK_POLICY_DB_TIMEOUT, isolation_level=None
    )
    try:
        if conn.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION:
            _initSchema(conn, pDbFile)
    except BaseException:
        conn.close()
        raise
    return conn


def _initSchema(pConn, pDbFile):
    """Create the schema in a new database; refuse one a newer version made"""
    # readers do not block the writer, nor the writer them (persistent,
    # and only possible outside a transaction)
    pConn.execute("PRAGMA journal_mode = WAL")
    pConn.execute("BEGIN IMMEDIATE")
    try:
        version = pConn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            pConn.execute(_schema())
            pConn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        elif version != _SCHEMA_VERSION:
            raise RuntimeError(
                f"policy database {pDbFile} has schema version {version}, this version of timekpr knows {_SCHEMA_VERSION}"
            )
        pConn.execute("COMMIT")
    except BaseException:
        pConn.execute("ROLLBACK")
        raise


def _importedRow(pTarget, pValues, pFile):
    """The row for the settings of a policy file of an earlier version,
    {setting: text}: text settings as they are (one that does not parse
    stays for the administrator to see, and is ignored as it was in the
    file), numbers and truth values converted; a setting the database
    cannot hold for the target is dropped and logged.  A file holding
    only one of the allowed days and their limits holds the other at its
    default, as it did."""
    isGroup = isGroupTarget(pTarget)
    row = {}
    for rParam in USER_CONFIG_PARAMS:
        value = pValues.get(rParam)
        if value is None:
            continue
        try:
            if rParam == ("HIDE_TRAY_ICON" if isGroup else "OVERRIDES"):
                raise ValueError(
                    f"a {'group' if isGroup else 'user'} policy cannot hold it"
                )
            kind = _COLUMN_TYPES[rParam]
            if kind == "INTEGER":
                value = int(value)
            elif kind == "BOOLEAN":
                value = readBool(value)
            else:
                value = value.strip().strip(";")
        except ValueError as ex:
            log.log(
                cons.TK_LOG_LEVEL_INFO,
                f"WARNING: {rParam} in policy file {pFile} is not imported ({ex})",
            )
            continue
        row[rParam] = value
    days, limits = "ALLOWED_WEEKDAYS", "LIMITS_PER_WEEKDAYS"
    for rSet, rMissing in ((days, limits), (limits, days)):
        if rSet in row and rMissing not in row:
            row[rMissing] = USER_CONFIG_DEFAULTS[rMissing]
    # result
    return row


class timekprPolicyStore:
    """The policies in a configuration directory and their resolution.

    The policies are rows of one SQLite database.  Every access is a
    transaction: a read sees the policies as one consistent snapshot, and
    a change (transaction(), which takes the database's write lock before
    reading) is applied whole or not at all, serialized with every other
    writer, the daemon's threads and processes alike.  A store may be used
    from several threads: the transaction in progress is per thread."""

    def __init__(self, pConfigDir):
        """Remember where the policies live"""
        self._configDir = pConfigDir
        self._dbFile = os.path.join(pConfigDir, cons.TK_POLICY_DB_FILE)
        # the connection of the transaction in progress, per thread
        self._local = threading.local()

    # ## transactions ##

    @contextlib.contextmanager
    def _session(self, pWrite):
        """A transaction (with pWrite, one that holds the write lock from
        its start, so that what it reads stays true until it commits),
        yielding its connection; within one, the store's methods take part
        in it.  It commits when the block ends and rolls back when the
        block raises."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            # already in one: take part in it
            if pWrite and not self._local.writing:
                raise RuntimeError("a policy change inside a read-only snapshot")
            yield conn
            return
        conn = _connect(self._dbFile)
        try:
            conn.execute("BEGIN IMMEDIATE" if pWrite else "BEGIN")
            self._local.conn, self._local.writing = conn, pWrite
            try:
                yield conn
            except BaseException:
                # (SQLite may have rolled back already, on a full disk say)
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                self._local.conn = None
            conn.execute("COMMIT")
        finally:
            conn.close()

    def transaction(self):
        """A change to the policies, applied whole or not at all (see
        _session); nested ones are part of the outer one"""
        return self._session(True)

    def snapshot(self):
        """A consistent view of the policies for several reads (see _session)"""
        return self._session(False)

    # ## policies ##

    def getConfigDir(self):
        """The configuration directory the policies live in"""
        return self._configDir

    def getDatabaseFile(self):
        """The policy database"""
        return self._dbFile

    @staticmethod
    def _row(pConn, pTarget):
        """A policy's row (its settings, in USER_CONFIG_PARAMS order), None
        if there is none"""
        return pConn.execute(
            "SELECT {} FROM policy WHERE target = ?".format(", ".join(_COLUMNS)),
            (pTarget,),
        ).fetchone()

    def loadPolicy(self, pTarget):
        """The policy of a user or a group target, as a timekprUserConfig
        (holding nothing and not present when there is none)"""
        config = timekprUserConfig(pTarget)
        with self.snapshot() as conn:
            row = self._row(conn, pTarget)
        if row is not None:
            config.setStoredValues(dict(zip(USER_CONFIG_PARAMS, row)))
        # result
        return config

    def _insertRow(self, pTarget, pValues):
        """Write a policy's row, {setting: value} (a setting not given is
        unset), replacing the one there is"""
        params = list(pValues)
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO policy (target{}) VALUES (?{})".format(
                    "".join(f", {rParam.lower()}" for rParam in params),
                    ", ?" * len(params),
                ),
                (pTarget, *[pValues[rParam] for rParam in params]),
            )

    def savePolicy(self, pConfig):
        """Write a policy (creating it if there is none yet)"""
        self._insertRow(pConfig.getPolicyTarget(), pConfig.getStoredValues())
        pConfig.setPolicyPresent(True)

    def deletePolicy(self, pTarget):
        """Remove the policy of a user or a group target; True if there was one"""
        with self.transaction() as conn:
            deleted = conn.execute(
                "DELETE FROM policy WHERE target = ?", (pTarget,)
            ).rowcount
        # result
        return deleted > 0

    def _targets(self):
        """Every target with a policy"""
        with self.snapshot() as conn:
            return [rRow[0] for rRow in conn.execute("SELECT target FROM policy")]

    def hasUserPolicy(self, pUserName):
        with self.snapshot() as conn:
            return self._row(conn, pUserName) is not None

    def hasGroupPolicy(self, pGroup):
        with self.snapshot() as conn:
            return self._row(conn, groupTarget(pGroup)) is not None

    def getGroupsWithPolicy(self):
        """The groups that have a policy, sorted"""
        return sorted(
            groupName(rTarget) for rTarget in self._targets() if isGroupTarget(rTarget)
        )

    def getUsersWithPolicy(self):
        """The users that have a policy, sorted"""
        return sorted(
            rTarget for rTarget in self._targets() if not isGroupTarget(rTarget)
        )

    # ## the policy files of earlier versions ##

    def _policyFiles(self):
        """The policy files of earlier versions: (target, path)"""
        files = []
        for rDir, rSample, rToTarget in (
            (self._configDir, cons.TK_USER_CONFIG_SAMPLE, lambda pName: pName),
            (
                os.path.join(self._configDir, cons.TK_GROUP_CONFIG_DIR),
                cons.TK_GROUP_CONFIG_SAMPLE,
                groupTarget,
            ),
        ):
            pattern = cons.TK_USER_CONFIG_FILE.replace(".%s.", r"\.(.*)\.")
            for rFile in sorted(
                glob(os.path.join(rDir, cons.TK_USER_CONFIG_FILE % "*"))
            ):
                # the sample file is not a policy
                if os.path.basename(rFile) == rSample:
                    continue
                name = re.sub(pattern, r"\1", os.path.basename(rFile))
                files.append((rToTarget(name), rFile))
        # result
        return files

    def importPolicyFiles(self):
        """Move the policy files of earlier versions into the database, in
        one transaction, and rename each file (and its backup) with
        TK_POLICY_IMPORTED_EXT afterwards; one that cannot be read is
        renamed with TK_POLICY_INVALID_EXT and imports nothing.  A target
        that has a policy in the database already keeps it.  Returns the
        targets imported."""
        imported, done, invalid = [], [], []
        files = self._policyFiles()
        if not files:
            return imported
        with self.transaction():
            for rTarget, rFile in files:
                if not isValidTarget(rTarget):
                    log.log(
                        cons.TK_LOG_LEVEL_INFO,
                        f"WARNING: policy file {rFile} is not named after a user or group, it is left alone",
                    )
                    continue
                values = readPolicyFile(rFile, rTarget)
                if values is None:
                    invalid.append(rFile)
                    continue
                done.append(rFile)
                if self.loadPolicy(rTarget).isPolicyPresent():
                    log.log(
                        cons.TK_LOG_LEVEL_INFO,
                        f'WARNING: "{rTarget}" has a policy in the database already, policy file {rFile} is not imported',
                    )
                    continue
                row = _importedRow(rTarget, values, rFile)
                # a user policy that sets nothing is no policy (a group's
                # is what makes the group known)
                if not isGroupTarget(rTarget) and all(
                    rValue is None for rValue in row.values()
                ):
                    continue
                self._insertRow(rTarget, row)
                imported.append(rTarget)
        # the files are kept, under another name, for the administrator
        for rFiles, rExt in (
            (done, cons.TK_POLICY_IMPORTED_EXT),
            (invalid, cons.TK_POLICY_INVALID_EXT),
        ):
            for rFile in rFiles:
                for rPath in (rFile, rFile + cons.TK_BACK_EXT):
                    if os.path.exists(rPath):
                        os.rename(rPath, rPath + rExt)
                log.log(
                    cons.TK_LOG_LEVEL_INFO,
                    f"policy file {rFile} "
                    + (
                        "imported into the database"
                        if rExt == cons.TK_POLICY_IMPORTED_EXT
                        else "cannot be read, nothing imported"
                    )
                    + f", renamed to {os.path.basename(rFile)}{rExt}",
                )
        # result
        return imported

    # ## leftovers of automatically created user policies ##

    def getDefaultUserPolicies(self):
        """The users whose policy is one an earlier version wrote for
        every user: it sets every setting, all of them to the defaults, so
        it restricts nothing but keeps the group policies from applying.
        (A policy that sets nothing is listed too: it does nothing.)"""
        users = []
        with self.snapshot():
            for rUser in self.getUsersWithPolicy():
                config = self.loadPolicy(rUser)
                if config.getUnreadableParams():
                    # a value the administrator has to look at: not a leftover
                    log.log(
                        cons.TK_LOG_LEVEL_INFO,
                        f'WARNING: the policy of user "{rUser}" has values that cannot be read ({", ".join(config.getUnreadableParams())}); it is left alone',
                    )
                    continue
                if config.isLegacyPolicy() or config.isEmptyPolicy():
                    users.append(rUser)
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
        with self.snapshot() if pDryRun else self.transaction():
            users = self.getDefaultUserPolicies()
            if not pDryRun:
                for rUser in users:
                    self.deletePolicy(rUser)
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
        return self.loadPolicy(groupTarget(pGroup))

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
        # one snapshot: the fingerprint describes exactly the policies
        # the resolution is made of
        with self.snapshot():
            # the user's own policy
            own = self.loadPolicy(pUserName)
            # the groups with a policy the user is in
            groups = self.getUserPolicyGroups(pUserName)
            # result
            return self._resolveLayers(
                pUserName,
                own if own.isPolicyPresent() else None,
                groups,
                self._fingerprint(pUserName, groups),
                False,
            )

    def resolveUnknownMembership(self, pUserName):
        """The policy for a user whose groups cannot be looked up and who was
        never resolved before: their own settings over the most restrictive
        merge of every group policy, since the user may be in any of the
        groups.  A directory that is down thus never lifts a restriction.
        The resolution has no fingerprint, so the next successful lookup
        replaces it."""
        with self.snapshot():
            own = self.loadPolicy(pUserName)
            # result
            return self._resolveLayers(
                pUserName,
                own if own.isPolicyPresent() else None,
                self.getGroupsWithPolicy(),
                None,
                True,
            )

    def _resolveLayers(self, pUserName, pOwn, pGroups, pFingerprint, pUnresolved):
        """Layer the user's own policy (None without one) over the given
        groups' policies into one configuration object with every setting
        filled, so that the rest of the daemon sees one ordinary
        configuration"""
        with self.snapshot():
            configs = {rGroup: self._loadGroupPolicy(rGroup) for rGroup in pGroups}
        applied = self._dropOverridden(
            pGroups, lambda pGroup: configs[pGroup].getUserOverrides()
        )
        effective = timekprUserConfig(pUserName)
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

    def _fingerprint(self, pUserName, pGroups):
        """What a resolution is made of: the stored rows of the user's own
        policy and of the given groups' policies (None for one that does
        not exist)"""
        with self.snapshot() as conn:
            return (
                self._row(conn, pUserName),
                tuple(
                    (rGroup, self._row(conn, groupTarget(rGroup))) for rGroup in pGroups
                ),
            )

    def fingerprint(self, pUserName):
        """The fingerprint resolve() would produce now, without resolving;
        cheap enough for a periodic check.  Raises timekprLookupError as
        resolve() does."""
        with self.snapshot():
            return self._fingerprint(pUserName, self.getUserPolicyGroups(pUserName))


class timekprPolicyListing:
    """One pass over many users and groups for the admin lists: the
    policies are read once, from one snapshot, and a user's groups are
    asked from NSS once, however many users they are consulted for.  Where
    a user's policy comes from is decided as timekprPolicyStore.resolve
    does."""

    def __init__(self, pStore):
        self._store = pStore
        with pStore.snapshot():
            # the users and groups with a policy
            self._usersWithPolicy = pStore.getUsersWithPolicy()
            self._groupsWithPolicy = pStore.getGroupsWithPolicy()
            # the group policies
            self._groupConfigs = {
                rGroup: pStore._loadGroupPolicy(rGroup)
                for rGroup in self._groupsWithPolicy
            }
        # the groups of every user asked about so far
        self._userGroups = {}

    def getUsersWithPolicy(self):
        """The users that have a policy, sorted"""
        return list(self._usersWithPolicy)

    def getGroupsWithPolicy(self):
        """The groups that have a policy, sorted"""
        return list(self._groupsWithPolicy)

    def getGroupConfig(self, pGroup):
        """A group's policy"""
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
        if pUserName in self._usersWithPolicy:
            source = TK_POLICY_SOURCE_USER
        elif applied:
            source = TK_POLICY_SOURCE_GROUP
        else:
            return TK_POLICY_SOURCE_DEFAULT
        # result
        return "{}:{}".format(source, ";".join(applied)) if applied else source
