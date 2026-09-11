# Proposal: targeting groups in addition to users

Status: scoping only, no code written.  Revision 2, after design review.
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

The proposed model, in one paragraph: a *policy* is a config file.  A
user policy is `timekpr.<user>.conf`, a group policy is
`groups/timekpr.<group>.conf`.  Neither is created automatically; a
policy exists because an admin created it, and it can be created
before the user or any group member has ever logged in.  A user's
effective policy is their own policy if one exists, otherwise the
most-restrictive merge of the group policies whose groups they belong
to, after a precedence graph declared in the group files has removed
overridden groups, otherwise the built-in defaults (no limits).  The
only enforcement action is logging the user out.  Accounting (time
spent, time left, "today" adjustments) stays per user.

The work splits into a prerequisite phase that changes how user
policies are created, and the group phase proper:

| Phase | Content | Estimate |
| --- | --- | --- |
| 0. Prerequisites | Stop auto-creating user config files; setters create on demand; a delete-policy command; lockout clamped to logout | about 3 person-days |
| 1. Group policies | Group files, membership, merge and precedence, D-Bus, CLI, GUI, tests | about 11 to 12 person-days |
| Optional | Physically remove the lock, suspend, suspendwake, shutdown code and UI | about 2 person-days |

A shared time budget across group members is not proposed; see "Not
proposed".

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

**User discovery.**  On startup the daemon walks `pwd.getpwall()`,
filters by UID range, shell and a name regexp, and creates config and
control files for every user that passes
([server/config/userhelper.py:55-79](../../server/config/userhelper.py#L55-L79),
[:118-152](../../server/config/userhelper.py#L118-L152)).
A user that arrives later (a domain user, or one created after
startup) gets both files on first login, because the loaders fall back
to writing defaults when the file is missing
([config.py:710-716](../../common/utils/config.py#L710-L716),
[:1289-1294](../../common/utils/config.py#L1289-L1294)).  The admin
user list is the set of config files on disk, not a live account list
([getSavedUserList, userhelper.py:155-218](../../server/config/userhelper.py#L155-L218)),
and every admin setter refuses a user without a config file
([configprocessor.py:31-47](../../server/config/configprocessor.py#L31-L47)).
No file imports `grp`.

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

**Enforcement.**  When continuous time left drops to the termination
time, the user enters a restriction list with a lockout type taken
from their config, one of `lock`, `suspend`, `suspendwake`,
`terminate`, `kill`, `shutdown`
([daemon.py:316-332](../../server/interface/dbus/daemon.py#L316-L332),
[constants.py:161-166](../../common/constants/constants.py#L161-L166)).
The restriction loop has one branch per type
([daemon.py:353-455](../../server/interface/dbus/daemon.py#L353-L455)),
backed by `lockUserSessions`, `suspendComputer`, `shutdownComputer`,
`findNextAvailableIntervalStart` and `setWakeUpByRTC`
([userdata.py:852](../../server/user/userdata.py#L852),
[:912](../../server/user/userdata.py#L912),
[manager.py:360-376](../../server/interface/dbus/logind/manager.py#L360-L376),
[userhelper.py:88-104](../../server/config/userhelper.py#L88-L104)).

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

## Design decisions

### D1. Group targets are `@<group>` in the existing user-name argument

Every admin setter, the CLI, and the GUI already carry a target string.
A leading `@` marks a group.  This keeps all D-Bus signatures, the
client proxy, and the CLI argument shapes unchanged.

A leading `@` cannot collide with a user timekpr manages:

- POSIX portable user names are drawn from `[A-Za-z0-9._-]`
  ([POSIX.1-2017 §3.437 and §3.282](https://pubs.opengroup.org/onlinepubs/9699919799/basedefs/V1_chap03.html)).
- shadow-utils `useradd` requires the first character to be one of
  `[a-zA-Z0-9_.]` ([shadow `lib/chkname.c`](https://github.com/shadow-maint/shadow/blob/master/lib/chkname.c)).
- systemd's relaxed mode, used for names from NSS, tolerates an
  embedded `@` and does not forbid a leading one
  ([systemd User/Group Name Syntax](https://systemd.io/USER_NAMES/)),
  so a leading `@` is representable in `/etc/passwd`.  Domain naming
  schemes put the `@` in the middle (`bob@idm.nixos.test`).
- Decisively, timekpr's own validity regexp requires the first
  character to be `[a-zA-Z0-9_.]`
  ([userhelper.py:34](../../server/config/userhelper.py#L34)), and a
  user failing it is treated as a system user and never tracked
  ([daemon.py:203-207](../../server/interface/dbus/daemon.py#L203-L207)).
  The `@` check belongs next to that regexp.

The same escaping already used for D-Bus object paths
(`Gio.dbus_escape_object_path`, [misc.py:37-42](../../common/utils/misc.py#L37-L42))
covers `@` if a group ever needs an object path, which this proposal
does not require.

### D2. Policy files are created only by admins

The daemon stops creating `timekpr.<user>.conf` on startup and on
first login.  A user config file exists if and only if an admin set
something for that user, so its presence *is* the "user policy
present" test, and no marker key is needed.  Consequences:

- Admin setters create the file on demand when the target has none
  (today they refuse, [configprocessor.py:31-47](../../server/config/configprocessor.py#L31-L47)).
  A policy can therefore be set for a user who has never logged in,
  or for a group none of whose members has logged in.
- A new command deletes a policy (`--deletepolicy <target>`), which is
  how an admin returns a user to group or default treatment.  Without
  it, an accidentally created user policy would shadow the group
  forever.
- The counters file `<user>.time` keeps being created automatically,
  on first login by the daemon and on demand by `setTimeLeft` for a
  user who has not logged in yet (today that setter also refuses,
  [configprocessor.py:49-65](../../server/config/configprocessor.py#L49-L65)).
  Counters are not policy.
- A user with no policy of any kind is tracked with the built-in
  defaults, which impose no limits.  That is the same outcome as today
  for an unconfigured user.
- The admin user list becomes the union of users with a policy file,
  users from `pwd.getpwall()` passing the validity check, and known
  members of groups with a policy file.  Each entry carries a
  provenance flag so the GUI can show "user policy", "group policy"
  or "default".
- Existing installations already have an auto-created file for every
  user.  After upgrade those count as explicit user policies with
  default values and would shadow any group policy.  Migration is
  handled in two parts:
  - `timekpra --migrate [--dry-run]` deletes every user policy file
    whose limit keys all equal the built-in defaults, and reports each
    deletion.  Such a file restricts nothing, so deleting it can only
    leave the user unchanged or bring them under a group policy; it can
    never loosen anything.  The comparison is on values, not bytes, so
    it also catches files that `_saveConfigFile` rewrote back to
    defaults.  Counters files are left alone.
  - At startup the daemon logs one warning per user policy file that
    restricts nothing, naming `--migrate` and `--deletepolicy`.
  Refusing to start on unmigrated files was considered and rejected: a
  screen-time daemon that does not start enforces nothing, which is the
  least restrictive possible outcome of a configuration problem.
  Declaring existing setups deprecated is compatible with both parts
  and is a documentation decision.
- The README sentence "user list is retrieved from your system and
  initial configuration is applied"
  ([README.md:184-190](../../README.md#L184-L190)) changes.

### D3. A group is a policy file; membership comes from NSS

There is no list of managed groups in `timekpr.conf`.  The groups
timekpr knows about are exactly those with a file in
`<TIMEKPR_CONFIG_DIR>/groups/`.  Creating `groups/timekpr.users.conf`
is the admin's explicit act of putting every member of `users` under
policy.

Membership is resolved from the user side.  `grp.getgrnam(g).gr_mem`
lists only explicit secondary members and only what the NSS backend
enumerates, which is unreliable for domain users: the test's Kanidm
user `bob@idm.nixos.test` is not returned by `pwd.getpwall()` before
first login ([AGENTS.md, "Things learned"](../../AGENTS.md)).
`os.getgrouplist(user, primary_gid)` walks NSS for one user, includes
the primary group, and works for Kanidm and SSSD users.  So the code
asks "which groups with a policy file is this user in", evaluated when
the user's `timekprUser` is created and whenever the set of group
files or their mtimes changes.

A group-to-members listing is wanted only by the admin UI, for
display.  It is best effort: the union of `gr_mem`, users whose primary
GID is the group, and `getgrouplist` over every user known to the admin
list.  The UI should label it as such.  Nothing on the server depends
on it.

Groups carry policy only.  Accounting is per user without exception:
`setTimeLeft` and `setPlayTimeLeft` refuse a `@group` target, the
"Info & today" tab has no group form, and there is no group counters
file.

### D4. Effective policy

For a user U:

1. If `timekpr.<U>.conf` exists, it is the policy.  Group policies are
   not consulted.
2. Otherwise let G be the set of groups with a policy file that U
   belongs to.  Remove from G every group that is overridden, directly
   or transitively, by another group in G (see D5).  If G is empty, the
   policy is the defaults.  If G has one element, that file is the
   policy.
3. Otherwise the policy is the per-key most-restrictive merge of the
   remaining files (table below).  This is the "logged out under any
   policy means logged out" rule: time left is monotone in every limit
   key, so the merged policy is at least as restrictive as each input,
   and evaluating the merge once gives the same outcome as evaluating
   each policy and taking the minimum, without N copies of the limit
   structure per user in `userdata.py`.

The resolver lives in `common/utils/config.py`, so that
`adjustLimitsFromConfig` ([userdata.py:239-336](../../server/user/userdata.py#L239-L336)),
the admin getters, and the client all see one effective policy through
the existing getters and nothing in the accounting loop changes.

Merge table.  Only keys that survive D6 and D7 appear.  "More
restrictive" means: for the same behaviour by the user, at least as
much time is charged and at least as many things are forbidden.

| Key | Most restrictive merge | Why |
| --- | --- | --- |
| `ALLOWED_WEEKDAYS` | intersection | fewer days |
| `ALLOWED_HOURS_n` | per hour: present only if present in all; start = max, end = min, dropped if empty; the unaccounted flag `!` is kept only if all inputs set it | an unaccounted hour is free time, so it is a relaxation |
| `LIMITS_PER_WEEKDAYS` | minimum per day | |
| `LIMIT_PER_WEEK`, `LIMIT_PER_MONTH` | minimum | |
| `TRACK_INACTIVE` | logical OR | with it on, a session that is logged in but not the active one on the seat (locked screen, switched to another user, idle hint set) still burns time; with it off, only the active, unlocked, non-idle session counts ([logind/user.py:150-224](../../server/interface/dbus/logind/user.py#L150-L224)) |
| `PLAYTIME_ENABLED` | logical OR | PlayTime is an *extra* limit on the listed processes inside the ordinary limits; when it is off the listed processes are just ordinary screen time ([README.md:304-330](../../README.md#L304-L330)).  Enabling it adds a constraint.  The one case where "enabled" is a relaxation is override mode, and that is handled by the next row |
| `PLAYTIME_LIMIT_OVERRIDE_ENABLED` | logical AND | in override mode the ordinary daily, weekly and monthly limits are charged only while a listed process runs and everything else is free time ([README.md:339-345](../../README.md#L339-L345), [userdata.py:856-866](../../server/user/userdata.py#L856-L866)); it is the least restrictive PlayTime setting, so `False` wins |
| `PLAYTIME_UNACCOUNTED_INTERVALS_ENABLED` | logical AND | `False` forbids the listed processes during `!` hours |
| `PLAYTIME_ALLOWED_WEEKDAYS` | intersection | |
| `PLAYTIME_LIMITS_PER_WEEKDAYS` | minimum per day | |
| `PLAYTIME_ACTIVITIES` | union of process masks | the list is the set of processes *subject to* the PlayTime limit (or, in override mode, the set that is charged), so a longer list restricts more; union is the additive merge |

A ranking exists for `HIDE_TRAY_ICON` as well (`True` withholds
information from the user), so it could be merged with OR if it is
ever admitted to group policy; D7 keeps it user-only.

Every remaining key has a well-defined most-restrictive value, so two
groups that are incomparable in the precedence graph never need a
tie-break.  That is a direct consequence of D6 and D7; with the lockout
type still in play there would have been keys with no natural order.

### D5. Precedence is a graph declared in the group files

Each group policy file may carry `OVERRIDES = <group>;<group>;...`
in its main section.  `OVERRIDES = kids` in `groups/timekpr.teens.conf`
means: for a user in both `teens` and `kids`, `kids` is dropped before
merging.  The relation is transitive.  Edges naming groups that have
no policy file, or that the user is not in, are ignored for that user.

This is a partial order, so adding or removing a group never requires
renumbering anything, and unrelated groups simply merge (D4 step 3).
The only extra machinery is a cycle check at load time: a cycle is
logged as a configuration error and, for the groups on the cycle, the
edges are ignored, which falls back to most-restrictive merging.  With
tens of groups at most, a depth-first search on each resolution is
free.

The GUI can show the relation as a list of "overrides" on the group
page; no graph drawing is needed.

### D6. The only enforcement action is logging out

The lockout types `lock`, `suspend`, `suspendwake`, `kill` and
`shutdown` are dropped from the policy model.  `terminate` (logind
`TerminateSession`) is the behaviour.  Killing processes, suspending
or powering off the machine are decisions for the service manager and
the login manager, not for timekpr.

Two further places kill processes today and fall under the same rule:

- **PlayTime enforcement.**  When a user's PlayTime is used up, the
  daemon terminates and then kills the matching processes
  ([daemon.py:272-274](../../server/interface/dbus/daemon.py#L272-L274),
  [playtime.py:431-442](../../server/user/playtime.py#L431-L442)).
  That is the whole enforcement mechanism of PlayTime; there is no
  logout variant.  The options are to leave PlayTime as it is and
  accept the exception, to make PlayTime exhaustion log the user out
  like any other limit, or to drop PlayTime.  This proposal keeps the
  PlayTime keys in the policy model so the merge is complete, and
  leaves the enforcement choice as an open question.
- **Leftover process cleanup.**  After terminating sessions the daemon
  schedules `killLeftoverUserProcesses`, which walks the process table
  with psutil and terminates the user's processes that were reparented
  to init ([manager.py:350-356](../../server/interface/dbus/logind/manager.py#L350-L356),
  [misc.py:169-238](../../common/utils/misc.py#L169-L238)).  logind
  does the same job when `KillUserProcesses=yes` is set in
  `logind.conf`, which is exactly "belongs with the login manager".
  Proposed: remove the cleanup and document the logind setting.  The
  NixOS test should then set `services.logind.killUserProcesses` so
  the "user has gone" assertion still holds.

Two ways to do it, in order of cost:

- **Ignore** (Phase 0): the resolver returns `terminate` regardless of
  what a file says, the setter accepts only `terminate`, the GUI hides
  the radio group ([admin.glade:2751-2899](../../resource/client/forms/admin.glade#L2751-L2899)),
  and the CLI help for `--setlockouttype` says so.  The dead branches
  in the restriction loop stay in the tree.
- **Remove** (optional phase): delete the `lock` and `suspend` branches
  ([daemon.py:379-455](../../server/interface/dbus/daemon.py#L379-L455)),
  `lockUserSessions`, `findNextAvailableIntervalStart`,
  `suspendComputer`, `shutdownComputer`, `setWakeUpByRTC`, the
  `WAKEUP_HOUR_INTERVAL` key, the `setLockoutType` method and its
  proxy, CLI and GUI counterparts, the README sections
  ([README.md:436-506](../../README.md#L436-L506)), and the
  corresponding message strings in eleven translations.

This repository is a fork of the Launchpad upstream.  Removal widens
the diff for any future upstream merge, which is why it is optional
and separate.

### D7. User-specific keys are ignored in group files

`HIDE_TRAY_ICON` is about one person's desktop, not about policy.  In
a group file it is ignored and the default (`False`) applies to members
without a user policy.  The GUI does not show it on a group page.

`WAKEUP_HOUR_INTERVAL` goes with D6.  Every other key in the user file
is a limit and is listed in the merge table.

### D8. Global settings and the default policy

Server-wide settings in `timekpr.conf` today: log level, poll and save
intervals, termination and warning times, tracked and excluded session
types, excluded users, the PlayTime master switch and the enhanced
activity monitor, and a default for `TRACK_INACTIVE`
([resource/server/timekpr.conf](../../resource/server/timekpr.conf)).
Those all stay global; none of them is a per-user limit.

Two things change with D2:

- `TIMEKPR_TRACK_INACTIVE` is today copied into each user file at
  creation ([config.py:203-204](../../common/utils/config.py#L203-L204)).
  With no auto-creation it has to become the value the resolver uses
  when no policy sets the key.  The same is true of every other
  built-in default.
- That makes "the defaults" a real policy level rather than a
  constant.  Proposed: a pseudo-group `@all` whose policy file, if
  present, applies to every tracked user without any NSS lookup.  It
  composes with D5 (`OVERRIDES = all` in a group that relaxes it) and
  with D4 (it merges most-restrictively with other groups unless
  overridden).  It replaces the need for a `[DEFAULTS]` section in
  `timekpr.conf` and a second file format.

`PLAYTIME_ACTIVITIES` is a candidate for a global seed list (the
games installed on the machine are the same for everyone).  With `@all`
that falls out for free: put the list in the `@all` policy and it
unions into every group merge.  It does not reach a user with their
own policy file, by D4 rule 1; if that is wanted, the user policy can
declare `OVERRIDES`-style additivity later, and this proposal does not
go there.

### D9. File layout

- User policy: `<config dir>/timekpr.<user>.conf`, unchanged.
- Group policy: `<config dir>/groups/timekpr.<group>.conf`, sections
  `[@<group>]` and `[@<group>.PLAYTIME]`.  The subdirectory keeps the
  existing user scan ([userhelper.py:183-192](../../server/config/userhelper.py#L183-L192))
  from seeing group files, and makes "which groups have a policy" one
  directory listing.  The `@` in the section name lets one config class
  use "section = target" for both kinds of file.
- Sample: `resource/server/timekpr.GROUP.conf` installed next to the
  existing `timekpr.USER.conf` sample, excluded from listings the same
  way.

## Phase 0: prerequisites

These change user-policy handling and enforcement without introducing
groups.  They are independently useful and independently testable.

### Server

- `server/config/userhelper.py`: `checkAndInitUsers` stops writing
  config files; it keeps validating users and creating counters files.
  `getSavedUserList` becomes the union described in D2 and returns a
  provenance flag per entry.  The `pwd.getpwall()` part is what it is
  today; the group part arrives in Phase 1.
- `common/utils/config.py`: `loadUserConfiguration` no longer calls
  `initUserConfiguration` when the file is missing
  ([config.py:710-716](../../common/utils/config.py#L710-L716)); it
  returns defaults in memory and reports "no file".  The
  `saveSpent` mtime check must tolerate a missing file
  ([userdata.py:661](../../server/user/userdata.py#L661),
  [getUserConfigLastModified, config.py:1146](../../common/utils/config.py#L1146)),
  and must also notice a file *appearing* while the user is logged in.
  Proposed: compare a policy fingerprint (file mtime or `None`) instead
  of a bare mtime.
- `server/config/configprocessor.py`: `loadAndCheckUserConfiguration`
  creates the file with defaults when asked to set something and the
  file is missing; `getSavedUserInformation` reports defaults plus
  `POLICY_SOURCE = default` when it is missing.  `checkAndSetTimeLeft`
  and `checkAndSetPlayTimeLeft` create the counters file on demand.
  `checkAndSetLockoutType` accepts only `terminate`.
- `server/interface/dbus/daemon.py`: new method `deletePolicy(s target)`
  on the user admin interface; after deleting, a logged-in user is
  re-resolved and notified.  The resolver clamps the lockout type.

### Clients

- CLI: `--deletepolicy`; `--userinfo` prints `POLICY_SOURCE`;
  `--setlockouttype` help text reduced.
- GUI: a "Delete policy" button next to the user selector; a
  provenance label; lockout radio group hidden.
- `timekprc`: no change.

### Tests and docs

- NixOS test: the per-user "unrestricted login first" step
  ([timekpr.py:88-90](../../nix/tests/timekpr.py#L88-L90)) is replaced
  by asserting that `--settimelimits` works *before* first login and is
  enforced at first login.  Add `--deletepolicy` followed by a
  surviving login.  The Kanidm user exercises the "no passwd entry
  yet" path.
- README: "User configuration" and "Additional options" sections.

### Effort

| Item | Estimate |
| --- | --- |
| Loader, fingerprint, create-on-demand | 1 day |
| User list union, `deletePolicy`, lockout clamp | 1 day |
| CLI, GUI, messages | 0.5 day |
| Tests, README | 0.5 day |

About three person-days.

## Phase 1: group policies

### Common (`common/utils/config.py`)

- Generalise `timekprUserConfig` so the constructor takes a file path
  and a section name rather than deriving both from a user name.  Group
  files then reuse the whole class, including `initUserConfiguration`,
  `saveUserConfiguration` and the roughly forty getters and setters
  ([config.py:674-1250](../../common/utils/config.py#L674-L1250)).
  Without this the group file needs a 600-line copy.  This is the main
  regression risk of the whole proposal because it sits under every
  read and write; the existing NixOS test covers the user path.
- Add the `OVERRIDES` key to the group variant (load, default, save,
  log), ignored in user files.
- Add `timekprPolicyResolver`: given a user name, the config dir and a
  membership function, produce one effective config object per D4 and
  D5, plus provenance (`user`, `group:<names>`, `default`) and a
  fingerprint (user file mtime or `None`, sorted `(group, mtime)` pairs
  for the groups considered).  The merge table lives here.

### Server

- `server/config/userhelper.py`: `getGroupsWithPolicy()` (directory
  listing), `getUserGroups(user)` via `os.getgrouplist`, best-effort
  `getGroupMembers(group)` per D3, `isGroupTarget(name)` per D1, and
  the group part of the admin user list.
- `server/config/configprocessor.py`: the constructor takes a target;
  for `@group` it operates on the group's config object.  The seventeen
  `checkAndSet*` methods only touch `self._timekprUserConfig`
  ([configprocessor.py:268-1039](../../server/config/configprocessor.py#L268-L1039)),
  so once the class is generalised this is a constructor change plus
  the `OVERRIDES` setter.  `getSavedUserInformation` for a user returns
  the *effective* policy with provenance keys `POLICY_SOURCE` and
  `GROUPS`; for `@group` it returns that file and no counters.
  Setting a user-specific key (`HIDE_TRAY_ICON`) on a group target is
  rejected with a message.
- `server/interface/dbus/daemon.py`:
  - `timekprUser` creation calls the resolver instead of loading the
    user file directly; `saveSpent` compares the fingerprint.
  - After any write to `@group`, or a `deletePolicy`, every logged-in
    `timekprUser` re-resolves; those whose effective policy changed get
    `adjustLimitsFromConfig(False)` so the client is notified.  A
    membership change in NSS is picked up at the next fingerprint
    check by re-running `getUserGroups` (cheap, one NSS call per user
    per poll; or cache with a short TTL).
  - `setTimeLeft` and `setPlayTimeLeft` return an error for a `@group`
    target (D3).
  - New getter `getGroupList` returning `[[group, member-count]]`, the
    count being best effort.
  - `getUserList` includes provenance.

### D-Bus interface

- No signature changes on existing methods.  `getUserInformation`
  returns two additional keys (additive).  New methods: `getGroupList`,
  `deletePolicy` (Phase 0), `setOverrides(s group, as groups)`.
- No change to `resource/server/dbus/timekpr.conf` or to the polkit
  policy, which only guards `pkexec timekpra` as a whole
  ([com.ubuntu.timekpr.pkexec.policy:7-16](../../resource/server/polkit/com.ubuntu.timekpr.pkexec.policy#L7-L16)).
- A new client against an old daemon gets `UnknownMethod` for the new
  getters and "user not found" for `@` targets; both already surface as
  ordinary error messages via `formatException`
  ([administration.py:110-122](../../client/interface/dbus/administration.py#L110-L122)).

### Admin CLI

- `--grouplist`, `--groupinfo @kids` (reuses `--userinfo` formatting),
  `--setoverrides @teens 'kids;guests'`.  `@group` accepted by every
  existing `--set...` command with no parsing change.
- `printUserConfig` prints `POLICY_SOURCE`, `GROUPS`, `OVERRIDES`.
- `common/constants/messages.py` and `constants.py`
  `TK_USER_ADMIN_COMMANDS` gain the entries.
- `client/interface/dbus/administration.py`: one proxy method per new
  D-Bus method.

### Admin GUI

This is the largest single piece.

- The selector lists users and groups; group rows are `@kids (n
  members, best effort)`.  Users show their provenance.
- Selecting a group loads its file into the same form and disables the
  "Info & today" tab entirely (accounting is per user, D3).
- Selecting a user with no user policy shows the effective policy
  read-only with a "Create user policy from this" button; the four
  apply paths ([admingui.py:1909-2248](../../client/gui/admingui.py#L1909-L2248))
  then write a user file.  "Delete policy" returns the user to group
  or default treatment.  The five `calculate...Availability` methods
  ([admingui.py:1574-1787](../../client/gui/admingui.py#L1574-L1787))
  need a read-only mode.
- A small "Overrides" list on the group page, editable.
- `HIDE_TRAY_ICON` hidden on group pages.
- New strings go into `resource/locale/timekpr.pot` and show
  untranslated until the eleven `.po` files are updated.

### Client (`timekprc`)

No change.  It only ever receives effective limits over the existing
signals.

### Packaging

- `debian/install` enumerates every Python file individually
  ([debian/install:47-110](../../debian/install#L47-L110)); add any new
  module and the `timekpr.GROUP.conf` sample.  `debian/postinst`
  creates `/var/lib/timekpr/config/groups`.
- The nixpkgs derivation generates a `setup.py` using
  `find_namespace_packages`, so new modules are picked up automatically
  ([nixpkgs pkgs/by-name/ti/timekpr/package.nix](https://github.com/NixOS/nixpkgs/blob/master/pkgs/by-name/ti/timekpr/package.nix)).
  Check how the NixOS module provisions `/var/lib/timekpr` and add the
  subdirectory there too.

### Tests

Extend `nix/tests/timekpr.nix` and `nix/tests/timekpr.py`:

- Local group `kids` containing `alice`; Kanidm POSIX group `kids`
  containing `bob` (the test already creates `posix_users` this way,
  [timekpr.py:126-128](../../nix/tests/timekpr.py#L126-L128)).  The
  Kanidm user is the important case: it exercises `getgrouplist`
  through `kanidm-unixd` for a user with no passwd enumeration.
- `--settimelimits @kids 0;...` before either has logged in: both
  logins terminated.
- `--settimelimits alice ...` with time: alice survives, bob still
  terminated (user policy wins).  `--deletepolicy alice`: alice
  terminated again.
- Second group `teens` with time, `--setoverrides @teens kids`, alice
  added to `teens`: alice survives.  Then remove the override: alice
  terminated (most-restrictive merge).
- `--settimeleft @kids + 300` is refused; `--settimeleft alice + 300`
  works as today.
- An `@all` policy with no time and no other files: both terminated.

Each VM iteration is about ten minutes without KVM.

### Effort

| Item | Estimate |
| --- | --- |
| Config class generalisation, resolver, merge, precedence graph | 3 days |
| User helper, config processor | 1 day |
| Daemon: resolution, fingerprint, propagation, fan-out, getters | 1.5 days |
| CLI, proxy, messages | 0.5 day |
| GUI | 3 days |
| Tests | 1.5 days |
| README, `.pot`, packaging | 1 day |

About eleven to twelve person-days on top of Phase 0.

## Optional: remove the non-logout code

Per D6, about two person-days: the server branches and helpers listed
there, `setLockoutType` and its proxy and CLI command, the glade radio
group and its handler ([admingui.py:2861](../../client/gui/admingui.py#L2861)),
the README sections, and the message strings.  Best done as its own
commit after Phase 1 so the diff against upstream stays reviewable.

## Not proposed: shared budget per group

A pooled counter ("the kids share three hours a day between them")
would need a third file type in the work dir, reconciliation between
every member's in-memory counters on every 3-second poll
([daemon.py:245-334](../../server/interface/dbus/daemon.py#L245-L334)),
and a rule for concurrent sessions.  The accounting engine in
`userdata.py` assumes one counters file and one set of counters per
user throughout.  This is a rewrite of the accounting core rather than
an extension, and nobody has asked for it.

## Open questions

1. **PlayTime enforcement kills processes** (D6).  Keep the exception,
   turn PlayTime exhaustion into a logout, or drop PlayTime.  The
   policy model and merge table are the same in all three cases; only
   `daemon.py:272-274` and `playtime.py` change.
2. **Leftover process cleanup** (D6).  Proposed removal in favour of
   logind's `KillUserProcesses`.
3. **Setting a user-specific key on a group.**  Reject (proposed) or
   silently ignore.
4. **`HIDE_TRAY_ICON` in group policy.**  User-only per D7; an OR merge
   is available if that changes.
5. **Group membership refresh cadence.**  One `getgrouplist` call per
   logged-in user per 3-second poll is cheap locally but goes to the
   identity daemon for domain users.  A short TTL (30 seconds, the same
   as the save interval) is the proposed compromise.

## Files touched, by component

| Component | Phase 0 | Phase 1 |
| --- | --- | --- |
| Server daemon | `server/interface/dbus/daemon.py` | same, plus `server/user/userdata.py` (fingerprint) |
| Server config | `server/config/userhelper.py`, `server/config/configprocessor.py`, `common/utils/config.py` | same, plus the resolver (new module or in `config.py`) |
| D-Bus interface | `deletePolicy` | `getGroupList`, `setOverrides`, two new result keys |
| Admin CLI | `client/admin/adminprocessor.py`, `client/interface/dbus/administration.py`, `common/constants/{constants,messages}.py` | same files |
| Admin GUI | `client/gui/admingui.py`, `resource/client/forms/admin.glade` | same files, larger change |
| Client | none | none |
| Packaging | `resource/server/timekpr.USER.conf` comments | `debian/install`, `debian/postinst`, `resource/server/timekpr.GROUP.conf`, NixOS state directory |
| Tests and docs | `nix/tests/timekpr.{nix,py}`, `README.md` | same, plus `resource/locale/timekpr.pot` |
