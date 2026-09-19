# Group targeting: design and implementation notes

Status: implemented on this branch. This document started as a scoping
proposal (revisions 1 to 3 are in the branch history); it now records
the model that was built, why, and where each part lives. Line numbers
refer to the tree at the time of writing.

## Summary

timekpr had no notion of a group. Every limit, every saved counter,
every D-Bus object and every admin command was keyed by one user name,
and the daemon created a configuration file for every account it found.
The only "group" in the tree was the `timekpr` POSIX group that grants
access to the admin interface. Upstream has no open bug or question
asking for group limits
([bugs.launchpad.net/timekpr-next](https://bugs.launchpad.net/timekpr-next/+bugs?field.searchtext=group));
the nearest request is [question 708545, "Admin GUI usage"](https://answers.launchpad.net/timekpr-next/+question/708545),
a supervisor of many users tired of repeating the same configuration.

The model now in place, in one paragraph: a _policy_ is a configuration
file an administrator created. A user policy is `timekpr.<user>.conf`,
a group policy is `groups/timekpr.<group>.conf`, addressed as `@<group>`
wherever the admin API takes a user name. Nothing is created on its
own; a setter creates the policy on demand, so limits can be set for a
user or group nobody has logged in as. A user's effective policy is
their own policy if the file exists, otherwise the most-restrictive
merge of the policies of the groups they belong to, after a precedence
relation declared in the group files has dropped overridden groups,
otherwise the defaults (no limits). The pseudo-group `all` matches
every user. The only enforcement action is logging the user out.
Accounting stays per user.

Two features were removed on the way, by decision of the project owner:
PlayTime (limits on named processes, enforced by killing them, and
trivially evaded) and every lockout type other than terminating the
session (lock, suspend, suspendwake, kill, shutdown, and the
leftover-process killer). Killing processes, suspending or powering
off belong to the login and service managers.

## Design decisions

### D1. `@<group>` in the existing user-name argument

Every admin setter, the CLI, the web API and the GUI already carried a
target string. A leading `@` marks a group. All D-Bus signatures, the
client proxy and the CLI argument shapes stayed unchanged. A leading
`@` cannot collide with a user timekpr manages:

- POSIX portable user names are drawn from `[A-Za-z0-9._-]`
  ([POSIX.1-2017 §3.437 and §3.282](https://pubs.opengroup.org/onlinepubs/9699919799/basedefs/V1_chap03.html));
- shadow-utils `useradd` requires the first character to be one of
  `[a-zA-Z0-9_.]` ([shadow `lib/chkname.c`](https://github.com/shadow-maint/shadow/blob/master/lib/chkname.c));
- systemd's relaxed mode tolerates an embedded `@` and does not forbid
  a leading one ([systemd User/Group Name Syntax](https://systemd.io/USER_NAMES/)),
  but domain naming schemes put it in the middle (`bob@idm.nixos.test`);
- decisively, timekpr's own validity regexp requires the first character
  to be `[a-zA-Z0-9_.]` ([server/config/userhelper.py:37-39](../../server/config/userhelper.py#L37-L39)),
  and a user failing it is never tracked.

The prefix is `cons.TK_GROUP_TARGET_PREFIX`; the helpers are
`isGroupTarget`, `groupName` and `groupTarget` in
[server/config/policy.py:46-62](../../server/config/policy.py#L46-L62).

### D2. Policies are created only by administrators

The daemon no longer writes `timekpr.<user>.conf` on startup or on
first login, so the presence of a user's file _is_ the "user policy
present" test, and no marker key is needed.

- `timekprUserConfig.loadUserConfiguration` returns whether the file
  exists and yields the defaults in memory when it does not
  ([common/utils/config.py:978](../../common/utils/config.py#L978)). An
  unreadable file is set aside as `.invalid` and counts as absent.
- Admin setters create the file on demand
  ([configprocessor.py:41](../../server/config/configprocessor.py#L41),
  `pCreate=True`). A policy can therefore be set for a user who has
  never logged in, or for a group none of whose members has.
- `deletePolicy` removes a user's or a group's policy
  ([configprocessor.py:902](../../server/config/configprocessor.py#L902),
  [daemon.py:778](../../server/interface/dbus/daemon.py#L778)); a user
  is then back under their group policies.
- The counters file `<user>.time` is still created when a user is
  first tracked, and on demand when extra time is granted to a user who
  was not yet tracked. Counters are not policy.
- The admin user list is the union of users with a policy file, users
  from `pwd.getpwall()` passing the validity check, known members of
  groups with a policy, and the users the daemon is tracking; each entry
  carries the policy's provenance
  ([userhelper.py:134](../../server/config/userhelper.py#L134)). A
  directory user, whom the system cannot enumerate, is therefore listed
  once they have a policy or are logged in, but can be administered by
  name at any time: `getUserInformation` answers the effective policy
  for every name NSS or the daemon knows, and "not found" for any other
  name without a policy (the web bridge turns that into a 404).

Migration of installations that already had a file per user: those
files count as user policies with default values and would shadow any
group policy. `timekpra --migratepolicies dry-run|delete` lists or
deletes the user policies whose every value is a default
([policy.py:168-200](../../server/config/policy.py#L168-L200)), and the
daemon warns about them at startup. Deleting such a file can only leave
the user unchanged or bring them under a group policy; it never loosens
anything. Refusing to start on unmigrated files was rejected: a
screen-time daemon that does not start enforces nothing.

### D3. A group is a policy file; membership comes from NSS

There is no list of managed groups in `timekpr.conf`. The groups
timekpr knows are exactly those with a file in `groups/`
([policy.py:132](../../server/config/policy.py#L132)). Creating
`groups/timekpr.users.conf` is the administrator's explicit act of
putting every member of `users` under policy.

Membership is resolved from the user's side with `os.getgrouplist`
([policy.py:65](../../server/config/policy.py#L65)), which works for
domain users whose identity provider does not enumerate (the test's
Kanidm user is not returned by `pwd.getpwall()`). A group-to-members
listing exists only for display and is best effort
([policy.py:214](../../server/config/policy.py#L214)). Kanidm presents
its groups under their SPN (`kids@idm.nixos.test`), so a policy on a
domain group is addressed by that name; the NixOS test reads bob's
group names from `id` rather than assuming them.

Groups carry policy only. Accounting is per user without exception:
`setTimeLeft` and `setHideTrayIcon` refuse a group target, and there is
no group counters file.

### D4. Effective policy

For a user U ([policy.py:280](../../server/config/policy.py#L280)):

1. If `timekpr.<U>.conf` exists, it is the policy.
2. Otherwise let G be the groups with a policy that U belongs to (plus
   `all` if it has a policy). Remove every group that another group in
   G overrides, directly or transitively (D5). If G is empty, the
   defaults apply.
3. Otherwise the per-key most-restrictive merge of the remaining files
   ([config.py:1499](../../common/utils/config.py#L1499)):

| Key                                 | Merge                                                                                                                                   |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `ALLOWED_WEEKDAYS`                  | intersection                                                                                                                            |
| `ALLOWED_HOURS_n`                   | per hour: present only if present in all; start = max, end = min, dropped if empty; the unaccounted flag `!` is kept only if all set it |
| `LIMITS_PER_WEEKDAYS`               | minimum per day (they are stored positionally against the allowed days, so the merge goes through a per-day map)                        |
| `LIMIT_PER_WEEK`, `LIMIT_PER_MONTH` | minimum                                                                                                                                 |
| `TRACK_INACTIVE`                    | logical OR (a session that is logged in but not the active one on the seat still burns time if any policy says so)                      |
| `HIDE_TRAY_ICON`                    | not merged: a user setting, ignored in group files                                                                                      |

Time left is monotone in every key, so evaluating the merge once gives
the same outcome as evaluating each policy and taking the minimum
("logged out under any policy means logged out"), and the rest of the
daemon sees one ordinary configuration object.

The resolution also yields a _fingerprint_ (the user file's mtime, and
the names and mtimes of the group files considered). The user object
recomputes it on every save and resolves again when it changed
([userdata.py:1077](../../server/user/userdata.py#L1077)); the daemon
does the same for every logged-in user after a group policy or a policy
deletion changed ([daemon.py:690](../../server/interface/dbus/daemon.py#L690)).

### D5. Precedence is a graph declared in the group files

A group policy file may carry `OVERRIDES = <group>;<group>;...`.
`OVERRIDES = kids` in `groups/timekpr.teens.conf` means: for a user in
both, `kids` is dropped before merging. The relation is transitive. It
is a partial order, so adding or removing a group renumbers nothing,
and unrelated groups simply merge. A cycle is logged as a configuration
error and no group is dropped ([policy.py:239](../../server/config/policy.py#L239)).
`timekpra --setoverrides '@teens' 'kids;all'` sets it; the web API and
the GUI expose it as a list.

### D6. The only enforcement action is logging out

`terminate` (logind `TerminateSession`) is the behaviour. The lockout
types and the leftover-process killer were removed, not merely ignored
(see the commit "Remove PlayTime and every enforcement action except
logging out"), as was PlayTime, whose enforcement killed processes and
whose matching was evadable: by default it compared the mask with
`readlink /proc/<pid>/exe`, which copying the binary defeats; the mode
its sample masks needed (Windows games under Proton) compared the
command line, which any process can rewrite.

With those gone every remaining key has a most-restrictive value, so
incomparable groups never need a tie-break.

### D7. File layout and packaging

- User policy: `<config dir>/timekpr.<user>.conf`, unchanged.
- Group policy: `<config dir>/groups/timekpr.<group>.conf`, section
  `[@<group>]`; the subdirectory keeps the user scan from seeing group
  files and makes "which groups have a policy" one directory listing.
  The daemon creates the directory when it writes the first group
  policy.
- Sample `resource/server/timekpr.GROUP.conf`, installed next to the
  user sample and excluded from listings the same way.
- `server/config/policy.py` is listed in `debian/install`; the nixpkgs
  derivation picks modules up through `find_namespace_packages`.

## What each component does

| Component  | Where                                                                                                                                                                                                                                                     |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Resolution | `server/config/policy.py` (store, membership, precedence, fingerprints); `common/utils/config.py` (policy file location, defaults, merge, deletion)                                                                                                       |
| Daemon     | `server/config/configprocessor.py` (targets, on-demand creation, provenance keys); `server/interface/dbus/daemon.py` (`getGroupList`, `setOverrides`, `deletePolicy`, `migratePolicies`, refresh); `server/user/userdata.py` (resolve, refresh on change) |
| D-Bus      | four new methods on `com.timekpr.server.user.admin`, existing polkit actions reused; `getUserList` entries carry provenance; `getUserInformation` returns `POLICY_SOURCE`/`POLICY_GROUPS` (users) or `OVERRIDES` (groups)                                 |
| CLI        | `--grouplist`, `--groupinfo`, `--setoverrides`, `--deletepolicy`, `--migratepolicies`; `@group` passes through every `--set` command                                                                                                                      |
| Web        | `/groups` routes, `DELETE .../policy`, `POST /policies/migrate`; a Groups page, provenance and delete buttons, a migration section (`docs/web-api.md`)                                                                                                    |
| Admin GUI  | groups in the selector, provenance label, delete policy, new group entry, overrides field                                                                                                                                                                 |
| Client     | unchanged: it receives effective limits over the existing signals                                                                                                                                                                                         |
| Tests      | `nix/tests/web/` (API, connector, browser); `nix/tests/timekpr.py` (`exercise` sets policies before first login; `exercise_groups` covers a local and a Kanidm group, merge, overrides, the web API)                                                      |
| Docs       | `README.md` "Policies: users and groups"; `docs/web-api.md` "Policies"                                                                                                                                                                                    |

## Open points

1. **Membership refresh cadence.** Membership is re-read with the
   fingerprint on every save interval (30 seconds by default), one
   `getgrouplist` call per logged-in user. Fine locally; an identity
   daemon answers it from its cache.
2. **Polkit rules and group targets.** The `user` detail a rule sees
   for a group target is the target itself (`@kids`), so a rule scoped
   to one user never authorizes group changes; group administration
   needs the general grant.
3. **Setting a user-only key on a group** is refused with a message
   (`setHideTrayIcon`, `setTimeLeft`), as is `setOverrides` on a user.
