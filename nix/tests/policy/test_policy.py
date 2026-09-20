"""The policy store, the configuration setters and the daemon's policy
refresh on a temporary configuration directory, with NSS monkeypatched:
no daemon, no D-Bus, no VM.

alice is in kids and users, bob in users only; kids has a policy of one
hour a day.  nss.broken simulates a directory that is down (every lookup
raises OSError); a name not in nss.users is unknown (KeyError, as NSS
reports it)."""

import grp
import os
import pwd
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
    (tmp_path / "config" / cons.TK_GROUP_CONFIG_DIR).mkdir(parents=True)
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


def test_setting_for_a_group_creates_its_policy_from_the_defaults(config, store):
    result, _message = processor(config, "@staff").checkAndSetTimeLimitForWeek(HOUR)
    assert result == 0
    policy = timekprUserConfig(store.getConfigDir(), "@staff")
    assert policy.loadUserConfiguration()
    assert policy.getUserWeekLimit() == HOUR
    assert day_limits(policy) == [DAY] * 7
    assert policy.getUserOverrides() == []


def test_first_setting_copies_the_effective_policy(config, store, kids_policy):
    # alice's limits come from kids
    assert store.resolve("alice").source == TK_POLICY_SOURCE_GROUP
    # hiding the tray icon is the first setting made for her
    result, _message = processor(config, "alice").checkAndSetHideTrayIcon(True)
    assert result == 0
    resolution = store.resolve("alice")
    assert resolution.source == TK_POLICY_SOURCE_USER
    assert resolution.config.getUserHideTrayIcon() is True
    # the inherited limit is kept, not reset to the default
    assert day_limits(resolution.config) == [HOUR] * 7
    assert not resolution.config.isDefaultPolicy()
    # the file is a complete policy that loads back the same way
    reloaded = timekprUserConfig(store.getConfigDir(), "alice")
    assert reloaded.loadUserConfiguration()
    assert day_limits(reloaded) == [HOUR] * 7
    # and no longer follows the group
    set_day_limits(config, "@kids", 2 * HOUR)
    assert day_limits(store.resolve("alice").config) == [HOUR] * 7


def test_first_setting_without_group_policies_copies_the_defaults(config, store):
    result, _message = processor(config, "bob").checkAndSetTrackInactive(True)
    assert result == 0
    resolution = store.resolve("bob")
    assert resolution.source == TK_POLICY_SOURCE_USER
    assert resolution.config.getUserTrackInactive() is True
    assert day_limits(resolution.config) == [DAY] * 7


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
    # a user with a policy of their own needs no lookup
    nss.broken = False
    set_day_limits(config, "bob", HOUR)
    nss.broken = True
    result, message = processor(config, "bob").checkAndSetTimeLimitForWeek(HOUR)
    assert result == 0, message


def test_reading_during_an_outage_is_refused(config, nss, kids_policy):
    nss.broken = True
    result, message, _info = processor(config, "alice").getSavedUserInformation(
        cons.TK_CL_INF_FULL, False
    )
    assert result == -1
    assert "cannot be looked up" in message


# ## resolution ##


def test_lookup_failures_are_not_empty_membership(store, nss, kids_policy):
    assert store.resolve("alice").source == TK_POLICY_SOURCE_GROUP
    assert store.resolve("bob").source == TK_POLICY_SOURCE_DEFAULT
    nss.broken = True
    with pytest.raises(timekprLookupError) as failure:
        store.resolve("alice")
    assert not failure.value.unknownUser
    with pytest.raises(timekprLookupError):
        store.fingerprint("bob")
    with pytest.raises(timekprLookupError) as unknown:
        nss.broken = False
        store.resolve("nobody")
    assert unknown.value.unknownUser


def test_unknown_membership_applies_every_group_policy(config, store, nss, kids_policy):
    set_day_limits(config, "@staff", 2 * HOUR)
    processor(config, "@staff").checkAndSetTimeLimitForWeek(5 * HOUR)
    nss.broken = True
    resolution = store.resolveUnknownMembership("bob")
    assert resolution.source == TK_POLICY_SOURCE_UNRESOLVED
    assert resolution.groups == ["kids", "staff"]
    assert resolution.getSourceDescription() == "unresolved:kids;staff"
    assert resolution.fingerprint is None
    # the most restrictive merge
    assert day_limits(resolution.config) == [HOUR] * 7
    assert resolution.config.getUserWeekLimit() == 5 * HOUR


def test_unknown_membership_without_group_policies(store, nss):
    nss.broken = True
    resolution = store.resolveUnknownMembership("bob")
    assert resolution.source == TK_POLICY_SOURCE_UNRESOLVED
    assert resolution.groups == []
    assert day_limits(resolution.config) == [DAY] * 7


def test_listing_reports_unresolved_users(config, store, nss, kids_policy):
    set_day_limits(config, "bob", HOUR)
    listing = store.startListing()
    assert listing.getSourceDescription("alice") == "group:kids"
    assert listing.getGroupMembers("kids", ["alice", "bob"]) == ["alice"]
    nss.broken = True
    listing = store.startListing()
    assert listing.getSourceDescription("alice") == TK_POLICY_SOURCE_UNRESOLVED
    # a policy of one's own needs no lookup
    assert listing.getSourceDescription("bob") == TK_POLICY_SOURCE_USER
    assert listing.getGroupMembers("kids", ["alice", "bob"]) == []


# ## the daemon's refresh ##


def tracked_user(store, name):
    """A timekprUser as the daemon holds it, without the bus"""
    user = object.__new__(timekprUser)
    user._timekprUserData = user._initUserLimits()
    user._timekprUserData[cons.TK_CTRL_UNAME] = name
    user._timekprPolicyStore = store
    user._timekprUserConfig = timekprUserConfig(store.getConfigDir(), name)
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


def test_a_malformed_policy_does_not_stop_the_migration_scan(config, store):
    # dave's policy restricts nothing, carol's has a limit that is not a number
    set_day_limits(config, "dave", DAY)
    set_day_limits(config, "carol", DAY)
    path = store.getUserPolicyFile("carol")
    with open(path) as handle:
        text = handle.read()
    assert "LIMITS_PER_WEEKDAYS = " in text
    with open(path, "w") as handle:
        handle.write(
            text.replace("LIMITS_PER_WEEKDAYS = ", "LIMITS_PER_WEEKDAYS = abc;", 1)
        )
    assert store.getDefaultUserPolicies() == ["dave"]
    # what the daemon does at startup
    store.warnAboutDefaultPolicies()
    # the migration deletes dave's policy and leaves carol's alone
    assert store.migrateDefaultPolicies(pDryRun=True) == ["dave"]
    assert store.hasUserPolicy("dave")
    assert store.migrateDefaultPolicies(pDryRun=False) == ["dave"]
    assert not store.hasUserPolicy("dave")
    assert store.hasUserPolicy("carol")
