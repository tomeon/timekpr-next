# Proposal: targeting groups in addition to users

Status: scoping only, no code written.
Scope basis: this repository at commit `5c6ee67` (timekpr-next 0.5.8 plus
the Nix flake).

## Summary

timekpr-next has no notion of a group anywhere.  Every limit, every
saved counter, every D-Bus object and every admin command is keyed by
a single user name.  The only "group" in the tree is the `timekpr`
POSIX group that grants password-less access to the admin interface
([resource/server/dbus/timekpr.conf:22-29](../../resource/server/dbus/timekpr.conf#L22-L29),
[README.md:158-176](../../README.md#L158-L176)).  Upstream has no open
bug or answered question asking for group limits either: a search of
the Launchpad bug tracker for "group" returns nothing
([bugs.launchpad.net/timekpr-next](https://bugs.launchpad.net/timekpr-next/+bugs?field.searchtext=group)),
and the only "group" question is about the admin privilege group
([question 819349](https://answers.launchpad.net/timekpr-next/+question/819349)).
The closest request is [question 708545, "Admin GUI usage"](https://answers.launchpad.net/timekpr-next/+question/708545),
where a supervisor of many users finds it tedious to repeat the same
configuration per user and the maintainer points at multi-day selection
in the GUI rather than any cross-user mechanism.

"Targeting groups" can mean three different features.  They differ by
an order of magnitude in cost, so this proposal separates them:

| Feature | Meaning | Recommendation |
| --- | --- | --- |
| A. Bulk apply | `timekpra --settimelimits @kids ...` writes the same setting into each member's own config | Phase 1, roughly 5 person-days |
| B. Inherited configuration | Members without their own settings follow a group config file; changes to the group propagate to logged-in members | Phase 2, roughly 8 to 10 person-days |
| C. Shared budget | One pool of time spent across all members | Out of scope, see "Not proposed" |

Accounting (time spent, time left, "today" adjustments) stays per user
in both phases.  Only configuration becomes group-addressable.

## How targeting works today

This is the ground the change has to be built on.  Everything below is
keyed by user name, so a group feature touches every layer.

**Identity and storage.**  A user is identified by the string that
logind reports ([server/interface/dbus/logind/manager.py:116](../../server/interface/dbus/logind/manager.py#L116)).
That string names two files: `timekpr.<user>.conf` in the config dir,
whose sections are `[<user>]` and `[<user>.PLAYTIME]`
([common/utils/config.py:683](../../common/utils/config.py#L683),
[:702](../../common/utils/config.py#L702),
[:751](../../common/utils/config.py#L751)), and `<user>.time` in the
work dir for counters
([common/utils/config.py:1261](../../common/utils/config.py#L1261)).
Both files are always written in full with every key present
([initUserConfiguration, config.py:805-909](../../common/utils/config.py#L805-L909)),
and saving rewrites only keys that already exist in the file
([_saveConfigFile, config.py:27-77](../../common/utils/config.py#L27-L77)).
There is no such thing as an unset key, which matters for inheritance
(see decision 3).

**User discovery.**  On startup the daemon walks `pwd.getpwall()`,
filters by UID range, shell and a name regexp, and creates config and
control files for every user that passes
([server/config/userhelper.py:55-79](../../server/config/userhelper.py#L55-L79),
[:118-152](../../server/config/userhelper.py#L118-L152)).
The admin user list is the set of config files on disk, not a live
account list
([getSavedUserList, userhelper.py:155-218](../../server/config/userhelper.py#L155-L218)).
No file imports `grp`.  The only "set of users" in the code base is
`TIMEKPR_USERS_EXCL`, a semicolon-separated list of names
([resource/server/timekpr.conf:34](../../resource/server/timekpr.conf#L34)).

**Runtime.**  Each logged-in user gets a `timekprUser` object holding
its own limits and counters
([server/interface/dbus/daemon.py:212-227](../../server/interface/dbus/daemon.py#L212-L227),
[server/user/userdata.py:25-55](../../server/user/userdata.py#L25-L55)),
and its own D-Bus object at `/com/timekpr/server/user/<escaped name>`
for signals to the client
([common/utils/notifications.py:17-60](../../common/utils/notifications.py#L17-L60),
[common/constants/constants.py:128](../../common/constants/constants.py#L128)).
Limits are loaded from the user's file by `adjustLimitsFromConfig`
([userdata.py:239-336](../../server/user/userdata.py#L239-L336)), and
external edits are picked up by comparing the file's mtime on every save
([saveSpent, userdata.py:653-694](../../server/user/userdata.py#L653-L694)).

**Admin surface.**  The `com.timekpr.server.user.admin` interface has
two getters and seventeen setters; every one takes `pUserName` as its
first argument, constructs a `timekprUserConfigurationProcessor` for
that name, writes the file, and if the user is logged in calls
`adjustLimitsFromConfig(False)` or `adjustTimeSpentFromControl` so the
client is told immediately
([daemon.py:567-1065](../../server/interface/dbus/daemon.py#L567-L1065),
[server/config/configprocessor.py:19-1105](../../server/config/configprocessor.py#L19-L1105)).
The client-side proxy mirrors this one method per call
([client/interface/dbus/administration.py:130-568](../../client/interface/dbus/administration.py#L130-L568)).
The CLI has one `--set...` command per setter, each taking the user
name as its first positional
([client/admin/adminprocessor.py:97-346](../../client/admin/adminprocessor.py#L97-L346),
[constants.py:334-355](../../common/constants/constants.py#L334-L355)).
The GUI has a user combo box fed by `getUserList` and four apply paths
that each call several per-user setters
([client/gui/admingui.py:916-953](../../client/gui/admingui.py#L916-L953),
[:1909-2248](../../client/gui/admingui.py#L1909-L2248)).

**D-Bus policy.**  Access is granted per interface name, not per
method ([resource/server/dbus/timekpr.conf](../../resource/server/dbus/timekpr.conf)).
New methods on an existing interface need no policy change.

## Design constraints that shape the proposal

1. **Resolve membership from the user side, not the group side.**
   `grp.getgrnam(name).gr_mem` lists only explicit secondary members
   and only what the NSS backend enumerates.  For domain users this is
   unreliable: the test's Kanidm user `bob@idm.nixos.test` is not
   returned by `pwd.getpwall()` before first login, which is why the
   daemon creates his config on first login rather than at startup
   ([AGENTS.md, "Things learned"](../../AGENTS.md)).  `os.getgrouplist(user, primary_gid)`
   walks NSS for one user and includes the primary group, and works for
   Kanidm and SSSD users.  So the question the code should ask is "which
   of the configured groups is this user in", evaluated when the user's
   `timekprUser` is created and when a group config changes, never
   "who is in this group" as the source of truth.  A group-to-members
   listing is still useful for the admin UI and for bulk apply, but it
   should be presented as best effort (the union of `gr_mem`, primary
   GIDs of users with a config file, and `getgrouplist` over users with
   a config file).

2. **Groups must be opt-in.**  Every user is in `users`, and admins are
   in `wheel` or `sudo`.  Treating every POSIX group as a targeting
   scope would make effective limits depend on accidental membership.
   Proposed: a new `TIMEKPR_GROUPS` key in `[SESSION]` of
   `/etc/timekpr/timekpr.conf` listing the groups timekpr manages, in
   priority order.  Groups not listed are invisible to timekpr.

3. **A group name can reuse the existing `pUserName` parameter.**
   The user-name regexp requires the first character to be
   alphanumeric, `_` or `.`
   ([userhelper.py:34](../../server/config/userhelper.py#L34)), so a
   leading `@` can never be a user.  Passing `@kids` through the
   existing seventeen setters keeps the D-Bus signatures, the client
   proxy and the CLI argument shapes unchanged.  Domain user names
   contain `@` but never start with it, so `bob@idm.nixos.test` stays
   unambiguous.

4. **Group config files must not collide with user config files.**
   `getSavedUserList` globs `timekpr.*.conf` and takes the middle part
   as a user name ([userhelper.py:183-192](../../server/config/userhelper.py#L183-L192)).
   Group files should live in a subdirectory,
   `<TIMEKPR_CONFIG_DIR>/groups/timekpr.<group>.conf`, with sections
   `[@<group>]` and `[@<group>.PLAYTIME]`, so the existing scan cannot
   mistake them for users and the same loader code can be reused with a
   different section name.

5. **The config file format has no "unset".**  Inheritance therefore
   needs an explicit marker in the user file.  Proposed: one new key in
   the `[<user>]` section, `CONFIG_SOURCE = user | group`, defaulting to
   `user` for existing files (the loader already writes defaults for
   missing keys, [config.py:788-792](../../common/utils/config.py#L788-L792),
   so upgrades are automatic).  Per-key inheritance is deliberately not
   proposed for the first version; see decision 3 below.

## Phase 1: bulk apply to `@group`

Goal: a supervisor can set the same configuration on every member of a
group in one command or one click.  Nothing changes at runtime; each
user's own file is written exactly as if the supervisor had targeted
them individually.

### Server

- `server/config/userhelper.py` (small): add `getTimekprGroups()`
  reading the new `TIMEKPR_GROUPS` key, `getGroupMembers(group)` as
  described in constraint 1, and `isGroupTarget(name)` for the `@`
  prefix.  Extend `timekprConfig` in `common/utils/config.py` with the
  key's load, default, save and log lines
  ([the pattern at config.py:226-228, 338-340, 417-418](../../common/utils/config.py#L226-L228)).
- `server/interface/dbus/daemon.py` (medium): wrap the seventeen
  user setters so that a `@group` target expands to the member list and
  applies the existing per-user path to each, collecting results.  The
  natural shape is one private helper `_forEachTarget(pTarget, fn)`
  called from each setter, so the per-method bodies barely change.
  `setTimeLeft` and `setPlayTimeLeft` expand the same way (each member's
  control file gets the adjustment).  Add `getGroupList` returning
  `[[group, member-count]]` on the same interface.  `getUserInformation`
  with a `@group` target is not meaningful in Phase 1 (there is no group
  file yet) and should return an error.
- `server/config/configprocessor.py`: no change.
- Error reporting: today a setter returns one `(result, message)` pair.
  For a group target, return the first failure's message prefixed with
  the member name, and continue applying to the remaining members.  A
  partial failure is more useful than an abort because the files that
  did get written are already visible to logged-in users.

### D-Bus interface

- No signature changes.  One new method, `getGroupList`, on
  `com.timekpr.server.user.admin`.  No change to
  `resource/server/dbus/timekpr.conf` or the polkit policy, which only
  guards `pkexec timekpra` as a whole
  ([resource/server/polkit/com.ubuntu.timekpr.pkexec.policy:7-16](../../resource/server/polkit/com.ubuntu.timekpr.pkexec.policy#L7-L16)).
- Versioning: a new client against an old daemon gets
  `org.freedesktop.DBus.Error.UnknownMethod` for `getGroupList` and a
  "user not found" result for `@kids` targets.  Both are already
  surfaced as ordinary error messages by `formatException`
  ([administration.py:110-122](../../client/interface/dbus/administration.py#L110-L122)).

### Admin CLI (`timekpra`)

- `client/admin/adminprocessor.py` (small): add `--grouplist`; accept
  `@group` wherever a user name is accepted (no parsing change needed,
  the string is passed through).  Add the command to
  `TK_USER_ADMIN_COMMANDS` with a help line in
  `common/constants/messages.py`.
- `client/interface/dbus/administration.py` (small): one new proxy
  method for `getGroupList`.

### Admin GUI (`timekpra` graphical)

- `client/gui/admingui.py` and `resource/client/forms/admin.glade`
  (medium): append groups to the existing `TimekprUserSelectionLS`
  list store as `@kids (n members)` rows, so the same combo and the same
  four apply paths work unchanged.  When a group is selected, disable
  the "Info & today" widgets and the `retrieveUserInfoAndConfig` call
  (no saved state to show), and blank the limit widgets rather than
  loading anything.  This is a deliberately thin UI: the supervisor
  fills in the form and presses Apply, and the values land in every
  member's file.
- New user-visible strings need entries in `resource/locale/timekpr.pot`
  and will show untranslated in the eleven shipped languages until the
  `.po` files are updated.

### Client (`timekprc`)

No change.  Members receive the usual `timeConfigurationChangedNotification`
from `adjustLimitsFromConfig(False)` when their file is written.

### Packaging and tests

- `debian/install` enumerates every Python file individually
  ([debian/install:47-110](../../debian/install#L47-L110)); Phase 1
  adds no new modules, so only the sample `timekpr.conf` changes.  The
  nixpkgs derivation generates a `setup.py` using
  `find_namespace_packages`, so new modules in later phases are picked
  up automatically
  ([nixpkgs pkgs/by-name/ti/timekpr/package.nix](https://github.com/NixOS/nixpkgs/blob/master/pkgs/by-name/ti/timekpr/package.nix)).
- `nix/tests/timekpr.nix` and `nix/tests/timekpr.py`: create a local
  group `kids` containing `alice`, and a Kanidm POSIX group `kids`
  containing `bob` (the test already creates `posix_users` this way,
  [timekpr.py:126-128](../../nix/tests/timekpr.py#L126-L128)).  Set
  `TIMEKPR_GROUPS = kids` in the patched `timekpr.conf`
  ([timekpr.nix:57-65](../../nix/tests/timekpr.nix#L57-L65)).  Add
  subtests: `--grouplist` shows `kids`; `--settimelimits @kids 0;...`
  terminates both logins; `--settimeleft @kids + 300` lets both survive.
  The domain user is the important case because it exercises
  `getgrouplist` through `kanidm-unixd`.
- README: a short "Groups" subsection under "User configuration".

### Effort

| Item | Estimate |
| --- | --- |
| Config key, membership helpers | 0.5 day |
| Daemon target expansion, `getGroupList` | 1 day |
| CLI, proxy, messages | 0.5 day |
| GUI (combo rows, disable today tab) | 1.5 days |
| NixOS test (two runs of about 10 minutes each without KVM per iteration) | 1 day |
| README, `.pot` | 0.5 day |

About five person-days.  Risk is low: the only behavioural change for
existing installs is a new key in `timekpr.conf`, which the loader
adds with an empty default.

## Phase 2: inherited group configuration

Goal: a group has its own configuration file; members with
`CONFIG_SOURCE = group` follow it, and editing the group updates
logged-in members immediately.  New accounts added to the group pick up
the limits without any per-user step.

### Common (`common/utils/config.py`)

- Generalise `timekprUserConfig` so the constructor takes the file
  path and section name rather than deriving both from a user name.
  Group files then reuse the whole class, including `initUserConfiguration`,
  `saveUserConfiguration` and all getters and setters (about 40 methods,
  [config.py:674-1250](../../common/utils/config.py#L674-L1250)).
  This is the single most valuable refactor in the proposal; without it
  the group file needs a 600-line copy.
- Add `CONFIG_SOURCE` to the user section (load, default, save, log).
- Add an effective-config resolver: after loading the user's own file,
  if `CONFIG_SOURCE = group`, find the first group in `TIMEKPR_GROUPS`
  that the user belongs to, load that group's file, and overlay every
  limit key from it.  Expose `getEffectiveGroup()` and the group file's
  mtime.  Putting the resolver here means `userdata.py`,
  `configprocessor.py` and the admin getters all see the effective
  values without further changes.
- Decide what a group file's `HIDE_TRAY_ICON`, `LOCKOUT_TYPE` and
  `WAKEUP_HOUR_INTERVAL` mean for members.  They are ordinary limit
  keys and should inherit like the rest; the proposal does not carve
  out exceptions.

### Server

- `server/config/userhelper.py`: extend `checkAndInitUsers` so that a
  newly created user file gets `CONFIG_SOURCE = group` when the user is
  in a configured group, and `user` otherwise.  Add
  `getSavedGroupList` scanning the `groups/` subdirectory.
- `server/config/configprocessor.py`: make
  `timekprUserConfigurationProcessor` accept a group target and operate
  on the group's config object.  The seventeen `checkAndSet*` methods
  only touch `self._timekprUserConfig`
  ([configprocessor.py:268-1039](../../server/config/configprocessor.py#L268-L1039)),
  so once the config class is generalised this is a constructor change.
  `getSavedUserInformation` gains three keys in its result dict:
  `CONFIG_SOURCE`, `EFFECTIVE_GROUP` and `GROUPS` (the configured groups
  the user is in).  For a `@group` target it returns the group's saved
  configuration and no control values.
- `server/interface/dbus/daemon.py`: after a write to `@group`, call
  `adjustLimitsFromConfig(False)` on every logged-in `timekprUser`
  whose effective group is that group, so they are notified.  Extend
  the external-edit detection in `saveSpent` to compare the group
  file's mtime as well as the user's.  Add `setConfigSource(user, source)`
  as one new D-Bus method (or fold it into an existing setter; a
  separate method is clearer).
- `server/user/userdata.py`: no change beyond the mtime comparison,
  because `adjustLimitsFromConfig` reads through the config getters.

### D-Bus interface

- `getUserInformation` returns three additional keys (additive, no
  signature change).  `getUserInformation("@kids", "S")` becomes valid.
- One new method `setConfigSource(s user, s source)`.
- Still no policy file change.

### Admin CLI

- `--groupinfo @kids` (reuses `--userinfo` output formatting).
- `--setconfigsource 'user' 'group|user'`.
- `printUserConfig` prints the three new keys.

### Admin GUI

- Group rows in the selector now load the group's configuration into
  the form like a user would, with the "Info & today" tab disabled.
- User view: a label "Configuration inherited from @kids" plus a
  toggle that calls `setConfigSource`, and greying out the limit widgets
  while inheriting.  Writing a limit for an inheriting user must either
  be refused or switch the user to `CONFIG_SOURCE = user` first
  (decision 4).  This is the largest single piece of GUI work because
  `applyUserConfig` and the four `calculate...ControlAvailability`
  methods all assume the widgets are editable
  ([admingui.py:1322-1486](../../client/gui/admingui.py#L1322-L1486),
  [:1574-1787](../../client/gui/admingui.py#L1574-L1787)).

### Client

No change.  The client only ever sees effective limits over the
existing signals.

### Packaging and tests

- `debian/install`: add the `groups/` sample file if one is shipped;
  `debian/postinst` should create `/var/lib/timekpr/config/groups`.
  The NixOS module in nixpkgs owns `/var/lib/timekpr` via `StateDirectory`
  or `tmpfiles`; check that the subdirectory is created on NixOS too.
- NixOS test: write a `@kids` config with no time, check both users are
  terminated without any per-user command; set `alice` to
  `CONFIG_SOURCE = user` and grant her time, check she survives while
  `bob` is still terminated.
- README: document the file layout, `CONFIG_SOURCE`, precedence.

### Effort

| Item | Estimate |
| --- | --- |
| Config class generalisation, `CONFIG_SOURCE`, resolver | 2 days |
| Config processor and user helper changes | 1 day |
| Daemon propagation, mtime watch, `setConfigSource` | 1 day |
| CLI, proxy, messages | 0.5 day |
| GUI inherit indicator, toggle, group editing | 2 to 3 days |
| Tests | 1 day |
| Docs, `.pot`, packaging | 1 day |

About eight to ten person-days on top of Phase 1.  The refactor of
`timekprUserConfig` is the main regression risk because it sits under
every read and write; the existing NixOS test covers the user path and
should be run after every step.

## Not proposed: shared budget per group

A pooled counter ("the kids share three hours a day between them")
would need a third file type in the work dir, reconciliation between
every member's in-memory counters on every 3-second poll
([daemon.py:245-334](../../server/interface/dbus/daemon.py#L245-L334)),
and a rule for concurrent sessions (two members logged in at once burn
the pool at double rate, or not).  The accounting engine in
`userdata.py` assumes one control file and one set of counters per
user throughout.  This is a rewrite of the accounting core rather than
an extension, and no user has asked for it.  It is listed here only so
that the phrase "group limits" is not read as promising it.

## Decisions needed before Phase 1

1. **Opt-in group list versus all POSIX groups.**  Proposal: opt-in via
   `TIMEKPR_GROUPS`.  Reason given in constraint 2.
2. **Precedence when a user is in several configured groups.**
   Proposal: first match in `TIMEKPR_GROUPS` order.  Alternative: refuse
   to inherit and log a warning.  Only matters in Phase 2.
3. **Whole-file versus per-key inheritance.**  Proposal: whole file
   (`CONFIG_SOURCE`).  Per-key inheritance needs a representation for
   "unset" in a format that always writes every key, which means either
   a sentinel value or a second marker key per limit.  It can be added
   later without changing the file layout.
4. **Writing a limit for an inheriting user.**  Proposal: the setter
   returns an error telling the supervisor to switch the source first.
   Auto-detaching is friendlier in the GUI but surprising from the CLI
   and from scripts.
5. **Wire syntax for group targets.**  Proposal: `@group` in the
   existing `pUserName` argument.  Alternative: a parallel set of
   `setGroup*` methods, which doubles the D-Bus surface and the proxy
   for no functional gain.
6. **Whether Phase 1 ships alone.**  Bulk apply solves the request in
   question 708545 by itself and leaves the runtime untouched.  Phase 2
   can follow once the file layout and membership rules have been
   exercised in the field.

## Files touched, by component

| Component | Phase 1 | Phase 2 |
| --- | --- | --- |
| Server daemon | `server/interface/dbus/daemon.py` | same, plus `server/user/userdata.py` (mtime) |
| Server config | `server/config/userhelper.py`, `common/utils/config.py` (one key) | `common/utils/config.py` (class generalisation, resolver), `server/config/configprocessor.py` |
| D-Bus interface | one new method | one more method, three new result keys |
| Admin CLI | `client/admin/adminprocessor.py`, `client/interface/dbus/administration.py`, `common/constants/{constants,messages}.py` | same files |
| Admin GUI | `client/gui/admingui.py`, `resource/client/forms/admin.glade` | same files, larger change |
| Client | none | none |
| Packaging | `resource/server/timekpr.conf`, `debian/install` (sample conf only) | `debian/install`, `debian/postinst`, NixOS state directory |
| Tests and docs | `nix/tests/timekpr.{nix,py}`, `README.md`, `resource/locale/timekpr.pot` | same |
