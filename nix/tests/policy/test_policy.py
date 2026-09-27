"""The policy store, the configuration setters and the daemon's policy
refresh on a temporary configuration directory, with NSS monkeypatched:
no daemon, no D-Bus, no VM.

alice is in kids and users, bob in users only (carol and dave too); kids
has a policy of one hour a day.  nss.broken simulates a directory that is down (every lookup
raises OSError); a name not in nss.users is unknown (KeyError, as NSS
reports it)."""

import grp
import os
import pwd
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from timekpr.common.constants import constants as cons
from timekpr.common.utils.config import timekprUserConfig
from timekpr.server.config.configprocessor import timekprUserConfigurationProcessor
from timekpr.server.config.policy import (
    TK_POLICY_SOURCE_DEFAULT,
    TK_POLICY_SOURCE_GROUP,
    TK_POLICY_SOURCE_UNRESOLVED,
    TK_POLICY_SOURCE_USER,
    timekprLookupError,
    timekprPolicyStore,
)
from timekpr.server.user.userdata import timekprUser

HOUR = 3600
DAY = 86400
GIDS = {"kids": 1001, "users": 100, "staff": 1002}


class Nss:
    """The users NSS knows, each with their groups"""

    def __init__(self):
        self.users = {
            "alice": ["kids", "users"],
            "bob": ["users"],
            "carol": ["users"],
            "dave": ["users"],
        }
        self.broken = False

    def getpwnam(self, name):
        if self.broken:
            raise OSError("the directory is down")
        if name not in self.users:
            raise KeyError(name)
        return SimpleNamespace(pw_name=name, pw_gid=GIDS[self.users[name][0]])

    def getgrouplist(self, name, gid):
        if self.broken:
            raise OSError("the directory is down")
        return [GIDS[group] for group in self.users[name]]

    def getgrgid(self, gid):
        for name, number in GIDS.items():
            if number == gid:
                return SimpleNamespace(gr_name=name, gr_gid=gid, gr_mem=[])
        raise KeyError(gid)

    def getgrnam(self, name):
        return SimpleNamespace(gr_name=name, gr_gid=GIDS[name], gr_mem=[])


@pytest.fixture
def nss(monkeypatch):
    fake = Nss()
    monkeypatch.setattr(pwd, "getpwnam", fake.getpwnam)
    monkeypatch.setattr(os, "getgrouplist", fake.getgrouplist)
    monkeypatch.setattr(grp, "getgrgid", fake.getgrgid)
    monkeypatch.setattr(grp, "getgrnam", fake.getgrnam)
    return fake


@pytest.fixture
def config(tmp_path):
    """What the configuration processor needs of the main configuration"""
    (tmp_path / "config").mkdir()
    (tmp_path / "work").mkdir()
    return SimpleNamespace(
        getTimekprConfigDir=lambda: str(tmp_path / "config"),
        getTimekprWorkDir=lambda: str(tmp_path / "work"),
    )


@pytest.fixture
def store(config, nss):
    return timekprPolicyStore(config.getTimekprConfigDir())


def processor(config, target):
    return timekprUserConfigurationProcessor(target, config)


def set_day_limits(config, target, seconds):
    result, message = processor(config, target).checkAndSetTimeLimitForDays(
        [seconds] * 7
    )
    assert result == 0, message


def day_limits(config):
    """The per-day limits of a loaded configuration"""
    return config.getUserLimitsPerWeekdays()


@pytest.fixture
def kids_policy(config, store):
    """The group policy of kids: one hour a day"""
    set_day_limits(config, "@kids", HOUR)
    assert store.hasGroupPolicy("kids")


# ## the setters ##


def own_policy(store, target):
    """A policy as it is stored (only what it sets)"""
    policy = store.loadPolicy(target)
    assert policy.isPolicyPresent()
    return policy


def database(store):
    """A connection to the policy database of its own, as another process
    (sqlite3 by hand, say) has"""
    return sqlite3.connect(store.getDatabaseFile(), isolation_level=None)


def stored_row(store, target):
    """The columns a policy's row sets, as the database holds them"""
    conn = database(store)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM policy WHERE target = ?", (target,)).fetchone()
    conn.close()
    return {key: value for key, value in zip(row.keys(), row) if value is not None}


def test_a_setting_creates_a_policy_holding_that_setting_alone(config, store):
    result, _message = processor(config, "@staff").checkAndSetTimeLimitForWeek(HOUR)
    assert result == 0
    policy = own_policy(store, "@staff")
    assert policy.getSetParams() == ["LIMIT_PER_WEEK"]
    assert policy.getUserWeekLimit() == HOUR
    # what it does not set answers the default
    assert day_limits(policy) == [DAY] * 7
    assert policy.getUserOverrides() == []
    # the row holds that setting and nothing else
    assert stored_row(store, "@staff") == {"target": "@staff", "limit_per_week": HOUR}


def test_a_first_setting_leaves_the_inherited_limits_alone(config, store, kids_policy):
    # alice's limits come from kids
    assert store.resolve("alice").source == TK_POLICY_SOURCE_GROUP
    # hiding the tray icon is the first setting made for her
    result, _message = processor(config, "alice").checkAndSetHideTrayIcon(True)
    assert result == 0
    assert own_policy(store, "alice").getSetParams() == ["HIDE_TRAY_ICON"]
    resolution = store.resolve("alice")
    assert resolution.source == TK_POLICY_SOURCE_USER
    assert resolution.groups == ["kids"]
    assert resolution.getSourceDescription() == "user:kids"
    assert resolution.config.getUserHideTrayIcon() is True
    assert day_limits(resolution.config) == [HOUR] * 7
    # and the group policy keeps applying to what she does not set
    set_day_limits(config, "@kids", 2 * HOUR)
    assert day_limits(store.resolve("alice").config) == [2 * HOUR] * 7


def test_an_own_setting_replaces_the_group_value(config, store, kids_policy):
    set_day_limits(config, "alice", 3 * HOUR)
    resolution = store.resolve("alice")
    assert resolution.source == TK_POLICY_SOURCE_USER
    assert day_limits(resolution.config) == [3 * HOUR] * 7
    # the other settings still merge from the group
    processor(config, "@kids").checkAndSetTimeLimitForWeek(5 * HOUR)
    assert store.resolve("alice").config.getUserWeekLimit() == 5 * HOUR
    # deleting the policy puts her back under the group
    assert store.deletePolicy("alice")
    assert not store.deletePolicy("alice")
    assert day_limits(store.resolve("alice").config) == [HOUR] * 7


def test_the_days_and_their_limits_go_together(config, store, kids_policy):
    # kids: one hour every day; alice gets weekdays only, so her policy has
    # the limits of those days too (the limits are positional)
    result, _message = processor(config, "alice").checkAndSetAllowedDays(
        ["1", "2", "3", "4", "5"]
    )
    assert result == 0
    policy = own_policy(store, "alice")
    assert policy.getSetParams() == ["ALLOWED_WEEKDAYS", "LIMITS_PER_WEEKDAYS"]
    assert policy.getUserAllowedWeekdays() == ["1", "2", "3", "4", "5"]
    assert day_limits(policy) == [HOUR] * 5
    effective = store.resolve("alice").config
    assert effective.getUserAllowedWeekdays() == ["1", "2", "3", "4", "5"]
    assert effective.getUserLimitForDay(6) == 0
    # limits set first take the effective days
    result, _message = processor(config, "bob").checkAndSetTimeLimitForDays([60] * 7)
    assert result == 0
    policy = own_policy(store, "bob")
    assert policy.getUserAllowedWeekdays() == [str(day) for day in range(1, 8)]
    assert day_limits(policy) == [60] * 7
    # unsetting one unsets both
    policy.unsetParam("LIMITS_PER_WEEKDAYS")
    assert policy.getSetParams() == []


def write_policy_file(store, target, text):
    """A policy file as an earlier version kept it"""
    directory = store.getConfigDir()
    if target.startswith(cons.TK_GROUP_TARGET_PREFIX):
        directory = os.path.join(directory, cons.TK_GROUP_CONFIG_DIR)
        os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, cons.TK_USER_CONFIG_FILE % target.lstrip("@"))
    with open(path, "w") as handle:
        handle.write(text)
    return path


def test_a_file_holding_half_of_the_day_limits(config, store):
    # a hand-edited file with limits but no days: the days are the default
    write_policy_file(
        store, "alice", "[alice]\nLIMITS_PER_WEEKDAYS = 60;60;60;60;60;60;60\n"
    )
    assert store.importPolicyFiles() == ["alice"]
    policy = own_policy(store, "alice")
    assert policy.getSetSettings() == ["allowed_days", "limits_per_day"]
    effective = store.resolve("alice").config
    assert effective.getUserAllowedWeekdays() == [str(day) for day in range(1, 8)]
    assert day_limits(effective) == [60] * 7
    # and the other way round: every day allowed, with the default limit
    store.deletePolicy("alice")
    write_policy_file(store, "alice", "[alice]\nALLOWED_WEEKDAYS = 6;7\n")
    assert store.importPolicyFiles() == ["alice"]
    effective = store.resolve("alice").config
    assert effective.getUserAllowedWeekdays() == ["6", "7"]
    assert effective.getUserLimitForDay(6) == DAY
    assert effective.getUserLimitForDay(1) == 0


def test_a_policy_round_trips(config, store, kids_policy):
    result, _message = processor(config, "@kids").checkAndSetAllowedHours(
        "1", {"9": {"STARTMIN": 15, "ENDMIN": 60, "UACC": False}}
    )
    assert result == 0
    processor(config, "@kids").checkAndSetOverrides(["all", "teens"])
    policy = own_policy(store, "@kids")
    assert policy.getSetParams() == [
        "ALLOWED_HOURS_1",
        "ALLOWED_WEEKDAYS",
        "LIMITS_PER_WEEKDAYS",
        "OVERRIDES",
    ]
    assert policy.getUserAllowedHours("1") == {
        "9": {"STARTMIN": 15, "ENDMIN": 60, "UACC": False}
    }
    assert policy.getUserAllowedHours("2") == policy.getUserAllowedHours("7")
    assert policy.getUserOverrides() == ["all", "teens"]
    assert stored_row(store, "@kids")["overrides"] == "all;teens"


def test_a_value_that_does_not_parse_is_ignored(config, store, kids_policy):
    # edited by hand
    conn = database(store)
    conn.execute(
        "UPDATE policy SET limits_per_weekdays = 'abc;' || limits_per_weekdays"
    )
    conn.close()
    policy = own_policy(store, "@kids")
    assert policy.getUnreadableParams() == ["LIMITS_PER_WEEKDAYS"]
    # the days keep their (default) limits
    assert policy.getSetParams() == ["ALLOWED_WEEKDAYS", "LIMITS_PER_WEEKDAYS"]
    # the daemon resolves without the bad value, the row is left alone
    assert day_limits(store.resolve("alice").config) == [DAY] * 7
    assert stored_row(store, "@kids")["limits_per_weekdays"].startswith("abc;")


@pytest.mark.parametrize(
    "statement",
    [
        # a number that is not one
        "UPDATE policy SET limit_per_week = 'soon' WHERE target = '@kids'",
        "UPDATE policy SET track_inactive = 2 WHERE target = '@kids'",
        # the days without their limits
        "UPDATE policy SET limits_per_weekdays = NULL WHERE target = '@kids'",
        # overrides for a user, the tray icon for a group
        "INSERT INTO policy (target, overrides) VALUES ('alice', 'kids')",
        "UPDATE policy SET hide_tray_icon = 1 WHERE target = '@kids'",
        "INSERT INTO policy (target) VALUES ('')",
    ],
)
def test_the_database_refuses_what_the_daemon_never_writes(
    store, kids_policy, statement
):
    conn = database(store)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(statement)
    conn.close()
    assert stored_row(store, "@kids")["limits_per_weekdays"] == ";".join(
        [str(HOUR)] * 7
    )


@pytest.mark.parametrize(
    "call",
    [
        lambda p: p.checkAndSetAllowedDays(["8"]),
        lambda p: p.checkAndSetAllowedDays(None),
        lambda p: p.checkAndSetAllowedHours("9", {}),
        lambda p: p.checkAndSetTimeLimitForDays(["x"]),
        lambda p: p.checkAndSetTimeLimitForWeek("soon"),
        lambda p: p.checkAndSetTimeLimitForMonth(None),
        lambda p: p.checkAndSetTrackInactive(None),
        lambda p: p.checkAndSetHideTrayIcon(None),
    ],
)
def test_a_refused_setting_creates_no_user_policy(config, store, kids_policy, call):
    result, message = call(processor(config, "alice"))
    assert result == -1, message
    assert not store.hasUserPolicy("alice")
    # the group policy still applies
    resolution = store.resolve("alice")
    assert resolution.source == TK_POLICY_SOURCE_GROUP
    assert day_limits(resolution.config) == [HOUR] * 7


def test_a_refused_setting_creates_no_group_policy(config, store):
    result, _message = processor(config, "@staff").checkAndSetAllowedDays(["8"])
    assert result == -1
    assert not store.hasGroupPolicy("staff")
    assert store.getGroupsWithPolicy() == []
    result, _message = processor(config, "@staff").checkAndSetOverrides(["a/b"])
    assert result == -1
    assert not store.hasGroupPolicy("staff")


def test_a_setting_for_an_unknown_user_is_refused(config, store):
    result, message = processor(config, "nobody").checkAndSetTimeLimitForWeek(HOUR)
    assert result == -1
    assert "not found" in message
    assert not store.hasUserPolicy("nobody")


def test_a_setting_during_an_outage_is_refused(config, store, nss, kids_policy):
    nss.broken = True
    result, message = processor(config, "alice").checkAndSetTimeLimitForWeek(HOUR)
    assert result == -1
    assert "cannot be looked up" in message
    assert not store.hasUserPolicy("alice")


def test_reading_during_an_outage_is_refused(config, nss, kids_policy):
    nss.broken = True
    result, message, _info = processor(config, "alice").getSavedUserInformation(
        cons.TK_CL_INF_FULL, False
    )
    assert result == -1
    assert "cannot be looked up" in message


def test_a_group_is_read_as_it_is_and_answers_defaults(config, store, kids_policy):
    result, message, info = processor(config, "@kids").getSavedUserInformation(
        cons.TK_CL_INF_FULL, False
    )
    assert result == 0, message
    assert list(info["LIMITS_PER_WEEKDAYS"]) == [HOUR] * 7
    assert info["LIMIT_PER_WEEK"] == 7 * DAY
    result, _message, _info = processor(config, "@staff").getSavedUserInformation(
        cons.TK_CL_INF_FULL, False
    )
    assert result == -1


def test_unsetting_a_setting(config, store, kids_policy):
    # alice: her own week limit and tray icon over the group
    processor(config, "alice").checkAndSetTimeLimitForWeek(2 * HOUR)
    processor(config, "alice").checkAndSetHideTrayIcon(True)
    processor(config, "alice").checkAndSetAllowedHours(
        "2", {"9": {"STARTMIN": 0, "ENDMIN": 60, "UACC": False}}
    )
    result, message, info = processor(config, "alice").getSavedUserInformation(
        cons.TK_CL_INF_FULL, False
    )
    assert result == 0, message
    assert list(info["POLICY_SETTINGS"]) == [
        "allowed_hours_2",
        "limit_per_week",
        "hide_tray_icon",
    ]
    # not a setting, not a user's setting, not set
    for setting, text in (
        ("limits", "not a policy setting"),
        ("overrides", "applies to groups only"),
        ("limit_per_month", "does not set limit_per_month"),
    ):
        result, message = processor(config, "alice").checkAndUnsetSetting(setting)
        assert result == -1 and text in message, message
    assert own_policy(store, "alice").getSetSettings() == [
        "allowed_hours_2",
        "limit_per_week",
        "hide_tray_icon",
    ]
    # every day's hours at once
    result, _message = processor(config, "alice").checkAndUnsetSetting("allowed_hours")
    assert result == 0
    assert own_policy(store, "alice").getSetSettings() == [
        "limit_per_week",
        "hide_tray_icon",
    ]
    # the group's value applies again
    result, _message = processor(config, "alice").checkAndUnsetSetting("limit_per_week")
    assert result == 0
    processor(config, "@kids").checkAndSetTimeLimitForWeek(5 * HOUR)
    assert store.resolve("alice").config.getUserWeekLimit() == 5 * HOUR
    # the last setting takes the user's policy with it
    result, _message = processor(config, "alice").checkAndUnsetSetting("hide_tray_icon")
    assert result == 0
    assert not store.hasUserPolicy("alice")
    assert store.resolve("alice").source == TK_POLICY_SOURCE_GROUP
    # a group keeps its (then empty) policy, and cannot lose the tray icon
    result, message = processor(config, "@kids").checkAndUnsetSetting("hide_tray_icon")
    assert result == -1 and "applies to users only" in message
    for setting in ("limits_per_day", "limit_per_week"):
        result, _message = processor(config, "@kids").checkAndUnsetSetting(setting)
        assert result == 0
    assert store.hasGroupPolicy("kids")
    assert own_policy(store, "@kids").getSetSettings() == []
    result, message = processor(config, "@staff").checkAndUnsetSetting("limit_per_week")
    assert result == -1 and "has no policy" in message


# ## resolution ##


def test_lookup_failures_are_not_empty_membership(config, store, nss, kids_policy):
    assert store.resolve("alice").source == TK_POLICY_SOURCE_GROUP
    assert store.resolve("bob").source == TK_POLICY_SOURCE_DEFAULT
    set_day_limits(config, "bob", HOUR)
    nss.broken = True
    with pytest.raises(timekprLookupError) as failure:
        store.resolve("alice")
    assert not failure.value.unknownUser
    # a policy of one's own does not settle what it does not set
    with pytest.raises(timekprLookupError):
        store.resolve("bob")
    with pytest.raises(timekprLookupError):
        store.fingerprint("bob")
    with pytest.raises(timekprLookupError) as unknown:
        nss.broken = False
        store.resolve("nobody")
    assert unknown.value.unknownUser


def test_unknown_membership_applies_every_group_policy(config, store, nss, kids_policy):
    set_day_limits(config, "@staff", 2 * HOUR)
    processor(config, "@staff").checkAndSetTimeLimitForWeek(5 * HOUR)
    processor(config, "bob").checkAndSetTimeLimitForMonth(9 * HOUR)
    nss.broken = True
    resolution = store.resolveUnknownMembership("bob")
    assert resolution.source == TK_POLICY_SOURCE_UNRESOLVED
    assert resolution.groups == ["kids", "staff"]
    assert resolution.getSourceDescription() == "unresolved:kids;staff"
    assert resolution.fingerprint is None
    # the most restrictive merge, under bob's own settings
    assert day_limits(resolution.config) == [HOUR] * 7
    assert resolution.config.getUserWeekLimit() == 5 * HOUR
    assert resolution.config.getUserMonthLimit() == 9 * HOUR


def test_unknown_membership_without_group_policies(store, nss):
    nss.broken = True
    resolution = store.resolveUnknownMembership("bob")
    assert resolution.source == TK_POLICY_SOURCE_UNRESOLVED
    assert resolution.groups == []
    assert day_limits(resolution.config) == [DAY] * 7


def test_listing_reports_unresolved_users(config, store, nss, kids_policy):
    set_day_limits(config, "bob", HOUR)
    processor(config, "alice").checkAndSetHideTrayIcon(True)
    listing = store.startListing()
    assert listing.getSourceDescription("alice") == "user:kids"
    assert listing.getSourceDescription("bob") == TK_POLICY_SOURCE_USER
    assert listing.getGroupMembers("kids", ["alice", "bob"]) == ["alice"]
    nss.broken = True
    listing = store.startListing()
    assert listing.getSourceDescription("alice") == TK_POLICY_SOURCE_UNRESOLVED
    assert listing.getSourceDescription("bob") == TK_POLICY_SOURCE_UNRESOLVED
    assert listing.getGroupMembers("kids", ["alice", "bob"]) == []


# ## the daemon's refresh ##


def tracked_user(store, name):
    """A timekprUser as the daemon holds it, without the bus"""
    user = object.__new__(timekprUser)
    user._timekprUserData = user._initUserLimits()
    user._timekprUserData[cons.TK_CTRL_UNAME] = name
    user._timekprPolicyStore = store
    user._timekprUserConfig = timekprUserConfig(name)
    user._timekprPolicySource = ""
    user._timekprPolicyLookupFailed = False
    # the limits are not sent anywhere here
    user.getTimeLimits = lambda: None
    return user


def monday_limit(user):
    return user._timekprUserData["1"][cons.TK_CTRL_LIMITD]


def test_the_daemon_keeps_the_last_policy_during_an_outage(
    config, store, nss, kids_policy
):
    alice = tracked_user(store, "alice")
    alice.adjustLimitsFromConfig()
    assert alice.getPolicySource() == "group:kids"
    assert monday_limit(alice) == HOUR
    # the directory goes down: nothing changes, however often it is asked
    nss.broken = True
    assert alice.refreshPolicyIfChanged() is False
    assert alice.refreshPolicyIfChanged() is False
    assert alice.getPolicySource() == "group:kids"
    assert monday_limit(alice) == HOUR
    # even a direct re-resolution keeps what was in force
    alice.adjustLimitsFromConfig()
    assert monday_limit(alice) == HOUR
    # the policy changes while the directory is down; the change lands
    # once the lookup works again
    set_day_limits(config, "@kids", 2 * HOUR)
    assert alice.refreshPolicyIfChanged() is False
    nss.broken = False
    assert alice.refreshPolicyIfChanged() is True
    assert monday_limit(alice) == 2 * HOUR
    assert alice.refreshPolicyIfChanged() is False


def test_a_user_never_resolved_gets_every_group_policy(config, store, nss, kids_policy):
    nss.broken = True
    bob = tracked_user(store, "bob")
    bob.adjustLimitsFromConfig()
    assert bob.getPolicySource() == "unresolved:kids"
    assert monday_limit(bob) == HOUR
    assert bob.refreshPolicyIfChanged() is False
    # the first successful lookup resolves for real: bob is not in kids
    nss.broken = False
    assert bob.refreshPolicyIfChanged() is True
    assert bob.getPolicySource() == TK_POLICY_SOURCE_DEFAULT
    assert monday_limit(bob) == DAY


# ## leftovers of automatically created policies ##


LEGACY_POLICY = """[DOCUMENTATION]
#### this is the user configuration file for timekpr-next

[{user}]
ALLOWED_HOURS_1 = 0;1;2;3;4;5;6;7;8;9;10;11;12;13;14;15;16;17;18;19;20;21;22;23
ALLOWED_HOURS_2 = 0;1;2;3;4;5;6;7;8;9;10;11;12;13;14;15;16;17;18;19;20;21;22;23
ALLOWED_HOURS_3 = 0;1;2;3;4;5;6;7;8;9;10;11;12;13;14;15;16;17;18;19;20;21;22;23
ALLOWED_HOURS_4 = 0;1;2;3;4;5;6;7;8;9;10;11;12;13;14;15;16;17;18;19;20;21;22;23
ALLOWED_HOURS_5 = 0;1;2;3;4;5;6;7;8;9;10;11;12;13;14;15;16;17;18;19;20;21;22;23
ALLOWED_HOURS_6 = 0;1;2;3;4;5;6;7;8;9;10;11;12;13;14;15;16;17;18;19;20;21;22;23
ALLOWED_HOURS_7 = 0;1;2;3;4;5;6;7;8;9;10;11;12;13;14;15;16;17;18;19;20;21;22;23
ALLOWED_WEEKDAYS = 1;2;3;4;5;6;7
LIMITS_PER_WEEKDAYS = {limits}
LIMIT_PER_WEEK = 604800
LIMIT_PER_MONTH = 2678400
TRACK_INACTIVE = False
HIDE_TRAY_ICON = False
"""


def write_legacy_policy(
    store, user, limits="86400;86400;86400;86400;86400;86400;86400"
):
    """A policy file an earlier version wrote, imported as the daemon does"""
    write_policy_file(store, user, LEGACY_POLICY.format(user=user, limits=limits))
    assert store.importPolicyFiles() == [user]


def test_only_legacy_policies_are_migrated(config, store, kids_policy):
    # dave's is a file an earlier version wrote (everything set, all
    # defaults); carol's sets one default value on purpose, to override
    # the group; bob's is empty
    write_legacy_policy(store, "dave")
    set_day_limits(config, "carol", DAY)
    store.savePolicy(timekprUserConfig("bob"))
    assert store.getDefaultUserPolicies() == ["bob", "dave"]
    # what the daemon does at startup
    store.warnAboutDefaultPolicies()
    assert store.migrateDefaultPolicies(pDryRun=True) == ["bob", "dave"]
    assert store.hasUserPolicy("dave")
    assert store.migrateDefaultPolicies(pDryRun=False) == ["bob", "dave"]
    assert not store.hasUserPolicy("dave")
    assert not store.hasUserPolicy("bob")
    assert store.hasUserPolicy("carol")
    assert day_limits(store.resolve("carol").config) == [DAY] * 7


def test_a_malformed_policy_does_not_stop_the_migration_scan(config, store):
    write_legacy_policy(store, "dave")
    write_legacy_policy(store, "carol", limits="abc")
    assert store.getDefaultUserPolicies() == ["dave"]
    store.warnAboutDefaultPolicies()
    assert store.migrateDefaultPolicies(pDryRun=False) == ["dave"]
    assert store.hasUserPolicy("carol")


# ## the policy files of earlier versions ##


def test_policy_files_are_imported_once(config, store, kids_policy):
    # a user's (with its backup, and a group-only setting), a group's (with
    # a user-only one), one for another name, one that does not parse, one
    # for a target the database has a policy for, and the sample
    alice = write_policy_file(
        store, "alice", "[alice]\nLIMIT_PER_WEEK = 3600\nHIDE_TRAY_ICON = yes\n"
    )
    with open(alice + cons.TK_BACK_EXT, "w") as handle:
        handle.write("[alice]\n")
    teens = write_policy_file(
        store, "@teens", "[@teens]\nOVERRIDES = kids\nHIDE_TRAY_ICON = true\n"
    )
    bob = write_policy_file(store, "bob", "[someone]\nLIMIT_PER_WEEK = 60\n")
    carol = write_policy_file(store, "carol", "not a policy file\n")
    kids = write_policy_file(store, "@kids", "[@kids]\nLIMIT_PER_WEEK = 60\n")
    sample = write_policy_file(store, "USER", "[USER]\nLIMIT_PER_WEEK = 60\n")
    assert store.importPolicyFiles() == ["alice", "@teens"]
    assert stored_row(store, "alice") == {
        "target": "alice",
        "limit_per_week": HOUR,
        "hide_tray_icon": 1,
    }
    assert stored_row(store, "@teens") == {"target": "@teens", "overrides": "kids"}
    # a file that sets nothing is no user policy, the database's policy stays
    assert not store.hasUserPolicy("bob")
    assert not store.hasUserPolicy("carol")
    assert "limit_per_week" not in stored_row(store, "@kids")
    # the files are kept under another name, the sample is left alone
    for path in (alice, alice + cons.TK_BACK_EXT, teens, bob, kids):
        assert not os.path.exists(path)
        assert os.path.isfile(path + cons.TK_POLICY_IMPORTED_EXT)
    assert os.path.isfile(carol + cons.TK_POLICY_INVALID_EXT)
    assert os.path.isfile(sample)
    # the next start has nothing to import
    assert store.importPolicyFiles() == []


# ## concurrent changes ##


def test_a_reader_is_not_blocked_by_a_writer(config, store, kids_policy):
    other = database(store)
    other.execute("BEGIN IMMEDIATE")
    other.execute("UPDATE policy SET limits_per_weekdays = '60;60;60;60;60;60;60'")
    # the committed policies, at once
    assert day_limits(store.resolve("alice").config) == [HOUR] * 7
    other.execute("COMMIT")
    other.close()
    assert day_limits(store.resolve("alice").config) == [60] * 7


def test_concurrent_changes_are_serialized(config, store, kids_policy):
    # another writer is setting alice's week limit
    other = database(store)
    other.execute("BEGIN IMMEDIATE")
    other.execute("INSERT INTO policy (target, limit_per_week) VALUES ('alice', 60)")
    results = []
    thread = threading.Thread(
        target=lambda: results.append(
            processor(config, "alice").checkAndSetTimeLimitForMonth(HOUR)
        )
    )
    thread.start()
    # the setter waits for the lock rather than reading the old policy
    thread.join(0.5)
    assert thread.is_alive()
    other.execute("COMMIT")
    other.close()
    thread.join(10)
    assert results == [(0, "")]
    # neither change is lost
    assert stored_row(store, "alice") == {
        "target": "alice",
        "limit_per_week": 60,
        "limit_per_month": HOUR,
    }


def test_a_change_gives_up_on_a_lock_held_too_long(config, store, monkeypatch):
    monkeypatch.setattr(cons, "TK_POLICY_DB_TIMEOUT", 0.2)
    other = database(store)
    other.execute("BEGIN IMMEDIATE")
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        processor(config, "alice").checkAndSetTimeLimitForWeek(HOUR)
    other.execute("ROLLBACK")
    other.close()
    assert not store.hasUserPolicy("alice")


# ## several settings at once ##


def test_changes_apply_together_or_not_at_all(config, store, kids_policy):
    hours = {"9": {"STARTMIN": 0, "ENDMIN": 30, "UACC": False}}
    # a refused setting takes the ones applied before it along
    for changes, refused in (
        ({"limit_per_week": HOUR, "allowed_days": ["9"]}, "allowed_days"),
        ({"limit_per_week": HOUR, "track_inactive": None}, "track_inactive"),
        ({"limit_per_week": HOUR, "limits_per_day": {"1": "x"}}, "limits_per_day"),
        ({"limit_per_week": HOUR, "overrides": ["kids"]}, "overrides"),
        ({"limit_per_week": HOUR, "bogus": 1}, "bogus"),
    ):
        result, message, name = processor(config, "alice").applyPolicyChanges(
            [], changes
        )
        assert (result, name) == (-1, refused), message
        assert not store.hasUserPolicy("alice")
    # the days and their limits are set together: the days given, the
    # limits given, and the other days keeping theirs (kids' hour)
    result, message, name = processor(config, "alice").applyPolicyChanges(
        [],
        {
            "allowed_days": ["4", "2", "4"],
            "limits_per_day": {"4": 60, "6": 30},
            "allowed_hours_2": hours,
            "limit_per_week": HOUR,
        },
    )
    assert (result, name) == (0, ""), message
    policy = own_policy(store, "alice")
    assert policy.getUserAllowedWeekdays() == ["2", "4"]
    assert policy.getUserLimitsByDay() == {"2": HOUR, "4": 60}
    assert policy.getUserAllowedHours("2") == hours
    assert policy.getUserWeekLimit() == HOUR
    # the settings taken out go first; the limits alone keep the days
    result, message, name = processor(config, "alice").applyPolicyChanges(
        ["limit_per_week"], {"limits_per_day": {"2": 30}}
    )
    assert (result, name) == (0, ""), message
    policy = own_policy(store, "alice")
    assert policy.getUserLimitsByDay() == {"2": 30, "4": 60}
    assert not policy.isSet("LIMIT_PER_WEEK")
    # taking out what the policy does not set refuses the whole change
    result, message, name = processor(config, "alice").applyPolicyChanges(
        ["limit_per_month"], {"track_inactive": True}
    )
    assert (result, name) == (-1, "limit_per_month")
    assert "does not set limit_per_month" in message
    assert not own_policy(store, "alice").isSet("TRACK_INACTIVE")


def test_a_group_change_creates_its_policy(config, store):
    # even an empty one: the policy is what makes the group known
    result, message, name = processor(config, "@teens").applyPolicyChanges([], {})
    assert (result, name) == (0, ""), message
    assert store.hasGroupPolicy("teens")
    assert own_policy(store, "@teens").getSetParams() == []
    # unless it is refused
    result, _message, name = processor(config, "@staff").applyPolicyChanges(
        [], {"hide_tray_icon": True}
    )
    assert (result, name) == (-1, "hide_tray_icon")
    assert not store.hasGroupPolicy("staff")
    # and a target that is not a name is refused before anything else
    result, _message, name = processor(config, "@a/b").applyPolicyChanges([], {})
    assert (result, name) == (-1, "")
