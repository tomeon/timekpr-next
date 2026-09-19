# timekpr web API

`timekprw` is a thin HTTP front end for the timekpr daemon. Every
endpoint translates directly into one or more calls on the daemon's
D-Bus interfaces (`com.timekpr.server.user.admin` and
`com.timekpr.server.admin`, see `server/interface/dbus/daemon.py`),
made through the same `timekprAdminConnector` that `timekpra` and the
GTK administration tool use. The backend keeps no state of its own;
the daemon's configuration files remain the source of truth.

The implementation lives in `web/`: `models.py` (the JSON shapes),
`bridge.py` (translation to and from the daemon), `app.py` (FastAPI
routes) and `timekprw.py` (the executable). The web UI in
`web/static/` is served by the same process. An OpenAPI document is
available at `/api/v1/openapi.json` and an interactive one at
`/api/v1/docs`.

## Running

`timekprw.service` runs `timekprw` as the unprivileged user `timekprw`
(created by `sysusers.d/timekprw.conf`; on NixOS declare it with
`extraGroups = ["timekpr"]`), a member of the `timekpr` group, which
the polkit rule shipped with the daemon authorizes for the
administration interfaces. The daemon looks that membership up
through NSS, so a `DynamicUser` with a supplementary group would not
do. Options (`--listen`,
`--token-file`, `--static-dir`, `--root-path`, `--no-auth`) can also be
given as environment variables `TIMEKPRW_<OPTION>`, for example through
`/etc/timekpr/timekprw.env`, which the unit reads if it exists.

### Listening

`--listen SPEC` (repeatable) accepts `HOST:PORT`, `[IPV6]:PORT`,
`unix:PATH` and `fd:N`. Every form ends up as a bound socket handed to
the web server: `timekprw` binds TCP and UNIX sockets itself (a UNIX
socket gets mode `0660` and, when possible, group `timekpr`), inherits
an `fd:N` socket somebody else bound, and takes sockets passed by
systemd socket activation (`LISTEN_FDS`, see `sd_listen_fds(3)`)
without any option. The default `127.0.0.1:8463` applies only when
nothing else is given or passed.

The package's `timekprw.socket` unit listens on
`/run/timekprw/timekprw.sock` (mode `0660`, group `timekpr`); a
drop-in adding `ListenStream=127.0.0.1:8463` serves TCP through it as
well. Because the unit carries the same name as the service, systemd
passes its sockets whenever the service starts, so the default TCP
listener is then not bound.

### Authentication

A connection over a UNIX domain socket `timekprw` bound, inherited or
received from systemd is trusted: being allowed to open the socket
(root, or the `timekpr` group, the same rule as the daemon's D-Bus
policy) is the access control. This means a reverse proxy pointed at
the socket (`proxy_pass http://unix:/run/timekprw/timekprw.sock;`)
gets full, unauthenticated access; either let the proxy do the
authentication, or run `timekprw --auth-unix` (or
`TIMEKPRW_AUTH_UNIX=true`) to require the token on UNIX sockets too.
Trust is decided per socket when it is bound; a request whose
connection the server cannot attribute to one of those sockets is
not trusted. On TCP every endpoint
except `/health` requires a bearer token (`Authorization: Bearer
<token>`), read from, in order: the file named by `--token-file`, the
systemd credential `token` (a drop-in with
`LoadCredential=token:/path/to/file`), or `/etc/timekpr/timekprw.token`.
The token file must be readable by the service, which runs as
`timekprw` in the `timekpr` group, and should be readable by nobody
else: `0640 root:timekpr`, or hand it over as the systemd credential
`token` as the NixOS test does. `timekprw` refuses to start when it
cannot read the file and warns when the file is world readable. With
a TCP listener and no token file the service refuses
to start unless `--no-auth` is given, which is only appropriate behind
a reverse proxy that authenticates, or on loopback. TLS is left to a
reverse proxy; `--root-path` is the prefix such a proxy strips.

On TCP the `Host` header must name `localhost`, `127.0.0.1`, `::1`,
an address given to `--listen`, or a host given to `--allowed-host`
(repeatable, or `TIMEKPRW_ALLOWED_HOSTS`); other requests get `421`.
This stops DNS rebinding, where a web page points a name it controls
at the loopback address to reach the service from a browser: the
browser still sends the attacker's name as `Host`. Behind a reverse
proxy, or when listening on a wildcard address, add the public name
with `--allowed-host`. `/health` needs no token; its answer is cached
for five seconds so that it cannot be used to flood the daemon.

### `timekpra` over HTTP

`timekpra --server URL [--token-file FILE] <command>` (or the
environment variables `TIMEKPRA_SERVER` and `TIMEKPRA_TOKEN_FILE`) runs
any `timekpra` command against `timekprw` instead of the daemon's D-Bus
interface. `URL` is `http://HOST:PORT[/PREFIX]`, `https://...` or
`unix:///run/timekprw/timekprw.sock`. Over TCP the token is read from
`--token-file`, falling back to `/etc/timekpr/timekprw.token` when that
exists. Output is identical to the D-Bus path: the client
(`client/interface/http/administration.py`) converts the API's JSON
back into the daemon's shapes with `common/utils/webapi.py`, the same
module `timekprw` uses in the other direction.

## Conventions

- Base path `/api/v1`. Request and response bodies are JSON.
- Usernames appear as a single percent-encoded path segment. Domain
  users such as `bob@idm.nixos.test` are addressed as
  `/users/bob%40idm.nixos.test`. The backend must pass the decoded
  name to D-Bus unchanged.
- All durations are integer seconds, timekpr's native unit. Weekdays
  are ISO numbers `1` (Monday) to `7` (Sunday), as timekpr uses them.
  Hours are `0`-`23`. Booleans are JSON booleans.
- Idempotent updates use `PATCH` (partial) or `PUT` (full replacement
  of a list). Operations that are not idempotent, namely adding or
  subtracting time, use `POST`.
- A successful write returns the updated resource, so the UI never
  needs a follow-up `GET`.
- Errors use RFC 9457 `application/problem+json`
  (<https://www.rfc-editor.org/rfc/rfc9457>):
  `{"type", "title", "status", "detail", "errors": [{"field", "message"}]}`.
  `errors` and `applied` are omitted when empty. A user-scoped request
  for a user the daemon does not list (see "Policies") is `404`, as is
  a read of a group that has no policy and a deletion of a policy that
  does not exist; a value the
  daemon refuses is `400` with its message as `detail`; `500` means
  the daemon accepted the request but failed to apply it (its log,
  `/var/log/timekpr.log`, has the reason; on NixOS `/etc/timekpr` is
  a read-only store path, so the daemon-wide settings cannot be
  changed there); `503` means the daemon (or the system bus) is not
  reachable and `502` that the daemon's D-Bus policy refused
  `timekprw`. `401` is a missing or wrong token.
- Authentication is a bearer token on every endpoint except
  `/health`, see "Running" above. The backend process itself must
  run as root or as a member of the `timekpr` group, because that is
  who the daemon's D-Bus policy admits to the admin interfaces
  (`server/interface/dbus/daemon.py`, comment above `setAllowedDays`).
- `PATCH` on a config resource is applied as a sequence of D-Bus
  setters, one per field, in a fixed order. The backend validates
  the whole body first. If a setter still fails part-way, the
  response is `400` with `errors` naming the failed field and
  `applied` listing the fields that were written before it.

## Policies

A _policy_ is a configuration file an administrator made. A user
policy (`timekpr.<user>.conf`) applies to that user alone; a group
policy (`groups/timekpr.<group>.conf`, addressed as `@<group>` by
`timekpra` and as `/groups/<group>` here) applies to every member of
the group that has no policy of their own. Nothing is created on
login: the first setting for a user or a group creates its policy,
so limits can be made for users who never logged in. A user's
effective configuration is, in this order: their own policy; else
the most-restrictive merge of the policies of the groups they belong
to (the pseudo-group `all` matches everyone), after dropping every
group that another matching group `overrides`; else the defaults.
`GET /api/v1/users/{username}` reports the effective configuration
with `policy_source` (`user`, `group` or `default`) and
`policy_groups` (the groups that were merged, in merge order). The
user list holds every user with a policy, every user of the system
and every known member of a group with a policy; group membership is
looked up through NSS, so it reaches domain users too. See
`server/config/policy.py`.

Deleting a user's policy (`DELETE /api/v1/users/{username}/policy`)
puts them back under their group policies or the defaults; their
counters stay. Earlier versions created a policy for every user on
first login, which now hides that user's group policies;
`POST /api/v1/policies/migrate` deletes (or, with `dry_run`, only
lists) the user policies whose every value is a default.

## Endpoints

### Service

| Method  | Path             | Purpose                                                                                                                  |
| ------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `GET`   | `/api/v1/health` | Daemon reachability and versions. `200 {"daemon": "ok", "timekpr_version": "0.5.8"}` or `503 {"daemon": "unreachable"}`. |
| `GET`   | `/api/v1/config` | Daemon-wide configuration (`getTimekprConfiguration`). No `timekpra` equivalent; the GTK admin UI uses this call.        |
| `PATCH` | `/api/v1/config` | Update any subset of the fields below. Each maps to one `setTimekpr*` setter.                                            |

Fields of `/api/v1/config` (names follow `TIMEKPR_*` keys returned by the daemon):

| Field                     | Type                                                  | D-Bus setter                      |
| ------------------------- | ----------------------------------------------------- | --------------------------------- |
| `log_level`               | int (1-3)                                             | `setTimekprLogLevel`              |
| `poll_time`               | seconds                                               | `setTimekprPollTime`              |
| `save_time`               | seconds                                               | `setTimekprSaveTime`              |
| `termination_time`        | seconds                                               | `setTimekprTerminationTime`       |
| `final_warning_time`      | seconds                                               | `setTimekprFinalWarningTime`      |
| `final_notification_time` | seconds                                               | `setTimekprFinalNotificationTime` |
| `session_types_tracked`   | list of strings, e.g. `["x11","wayland","mir","tty"]` | `setTimekprSessionsCtrl`          |
| `session_types_excluded`  | list of strings                                       | `setTimekprSessionsExcl`          |
| `users_excluded`          | list of usernames                                     | `setTimekprUsersExcl`             |

### Users

| Method   | Path                                                  | `timekpra`                    | Purpose                                                                                                                                                                                                                                                |
| -------- | ----------------------------------------------------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `GET`    | `/api/v1/users`                                       | `--userlist`                  | The users timekpr knows (see "Policies"). Returns `[{"username", "full_name", "policy_source"}]`; `policy_source` is `user`, `group:<g1>;<g2>` or `default`. Query `?include=status` adds each user's `status` object (one extra D-Bus call per user). |
| `GET`    | `/api/v1/users/{username}`                            | `--userinfo` + `--userinfort` | Full view: `{"username", "config": {...}, "status": {...}, "policy_source", "policy_groups"}` (`getUserInformation(name, "F")`); `config` is the effective configuration.                                                                              |
| `GET`    | `/api/v1/users/{username}/config`                     | `--userinfo`                  | Saved configuration only (`"S"`).                                                                                                                                                                                                                      |
| `PATCH`  | `/api/v1/users/{username}/config`                     | all `--set*` except time left | Partial update, see field table below.                                                                                                                                                                                                                 |
| `GET`    | `/api/v1/users/{username}/status`                     | `--userinfort`                | Realtime counters (`"R"`).                                                                                                                                                                                                                             |
| `PUT`    | `/api/v1/users/{username}/config/allowed-hours/{day}` | `--setallowedhours`           | Replace the allowed hours for one weekday, or for every weekday when `{day}` is `all`.                                                                                                                                                                 |
| `POST`   | `/api/v1/users/{username}/time-left`                  | `--settimeleft`               | Add, subtract or set today's remaining time.                                                                                                                                                                                                           |
| `DELETE` | `/api/v1/users/{username}/policy`                     | `--deletepolicy`              | Delete the user's own policy (`deletePolicy`); `204`, or `404` when there is none. The user's group policies or the defaults apply again; the counters stay.                                                                                           |

There is no `POST /users`: a user is not created, the first setting
made for a name creates that name's policy (`PATCH .../config` on a
user without one is not `404`; the daemon answers the effective
configuration for any user it lists).

### Groups

A group is addressed by its plain name (no `@`), percent-encoded like
a username; `all` is the pseudo-group of everyone.

| Method   | Path                                                | `timekpra`                          | Purpose                                                                                                                                                                                                |
| -------- | --------------------------------------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `GET`    | `/api/v1/groups`                                    | `--grouplist`                       | The groups with a policy (`getGroupList`): `[{"group", "overrides": [...], "members": [...]}]`. `members` is best effort: an identity provider need not enumerate a group.                             |
| `GET`    | `/api/v1/groups/{group}`                            | `--groupinfo`                       | `{"group", "config": {...}}` (`getUserInformation("@group", "F")`); `404` when the group has no policy.                                                                                                |
| `GET`    | `/api/v1/groups/{group}/config`                     | `--groupinfo`                       | The policy alone; `404` when there is none.                                                                                                                                                            |
| `PATCH`  | `/api/v1/groups/{group}/config`                     | the `--set*` commands with `@group` | Partial update, fields below. The first setting for a group creates its policy, so this is never `404`; `overrides` goes through `setOverrides`, the rest through the same setters as a user's config. |
| `PUT`    | `/api/v1/groups/{group}/config/allowed-hours/{day}` | `--setallowedhours @group`          | As for users.                                                                                                                                                                                          |
| `DELETE` | `/api/v1/groups/{group}/policy`                     | `--deletepolicy @group`             | `204`, or `404` when there is none.                                                                                                                                                                    |
| `POST`   | `/api/v1/policies/migrate`                          | `--migratepolicies`                 | Body `{"dry_run": true}` (the default); returns `{"users": [...]}`, the users whose default-valued policy was (or, with `dry_run`, would be) deleted.                                                  |

A group's `config` has the fields of a user's `config` except
`hide_tray_icon` (a per-user preference), plus `overrides`: a list of
group names whose policies this one takes precedence over for users
in both. `PATCH` accepts any subset of them.

#### `config` resource

`GET /api/v1/users/{username}/config` returns the user's effective
configuration, and `PATCH` accepts any subset of the fields below;
a `PATCH` on a user without a policy of their own creates one (from
the defaults, not from the group policies that applied before). The
fields are:

| Field                                                                      | Type                                                     | D-Bus setter                          | Notes                                                                                                                                                                                                                                                                                                                        |
| -------------------------------------------------------------------------- | -------------------------------------------------------- | ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `allowed_days`                                                             | list of weekdays, e.g. `[1,2,3,4,5]`                     | `setAllowedDays`                      |                                                                                                                                                                                                                                                                                                                              |
| `limits_per_day`                                                           | object weekday → seconds, `{"1": 7200, ..., "7": 10800}` | `setTimeLimitForDays`                 | The daemon stores limits positionally against `allowed_days` (`server/user/userdata.py`), so `GET` lists only allowed days, keys for other days are ignored, missing days keep their current value, and a change of `allowed_days` re-sends the limits aligned with the new days. Values are clamped to 86400 by the daemon. |
| `allowed_hours`                                                            | object weekday → list of hour entries (below)            | `setAllowedHours`, once per day given | Same shape as the `PUT` sub-resource; `PATCH` is for editing several days in one request.                                                                                                                                                                                                                                    |
| `limit_per_week`                                                           | seconds                                                  | `setTimeLimitForWeek`                 |                                                                                                                                                                                                                                                                                                                              |
| `limit_per_month`                                                          | seconds                                                  | `setTimeLimitForMonth`                |                                                                                                                                                                                                                                                                                                                              |
| `track_inactive`                                                           | bool                                                     | `setTrackInactive`                    |                                                                                                                                                                                                                                                                                                                              |
| `hide_tray_icon`                                                           | bool                                                     | `setHideTrayIcon`                     |                                                                                                                                                                                                                                                                                                                              |
| An hour entry is                                                           |
| `{"hour": 11, "start_minute": 0, "end_minute": 30, "unaccounted": false}`. |
| `start_minute` defaults to `0`, `end_minute` to `60`, `unaccounted`        |
| to `false`, so the CLI string `7;8;11[00-30];!14` becomes                  |

```json
[
  { "hour": 7 },
  { "hour": 8 },
  { "hour": 11, "start_minute": 0, "end_minute": 30 },
  { "hour": 14, "unaccounted": true }
]
```

This replaces the `;`/`[]`/`!` mini-language with structure the UI
can render directly; the backend converts to the daemon's
`{hour: {STARTMIN, ENDMIN, UACC}}` map.

Example `GET /api/v1/users/alice/config`:

```json
{
  "allowed_days": [1, 2, 3, 4, 5, 6, 7],
  "limits_per_day": {
    "1": 7200,
    "2": 7200,
    "3": 7200,
    "4": 7200,
    "5": 7200,
    "6": 10800,
    "7": 10800
  },
  "allowed_hours": {
    "1": [{ "hour": 7 }, { "hour": 8 }],
    "2": [],
    "3": [],
    "4": [],
    "5": [],
    "6": [],
    "7": []
  },
  "limit_per_week": 50000,
  "limit_per_month": 200000,
  "track_inactive": false,
  "hide_tray_icon": false
}
```

#### `status` resource

Realtime values exist only while the daemon is tracking a session of
the user; otherwise the `actual_*` fields are `null` and only the
counters persisted in the user's control file are populated. Field
names follow the daemon's keys (`ACTUAL_*` from
`_getUserActualTimeInformation`, the rest from
`getSavedUserInformation`).

```json
{
  "session_active": true,
  "time_spent_balance": 1234,
  "time_spent_day": 1300,
  "time_spent_week": 5400,
  "time_spent_month": 20000,
  "time_left_day": 5966,
  "time_left_continuous": 3600,
  "time_spent_session": 900,
  "time_inactive_session": 60
}
```

#### Time-left actions

`POST /api/v1/users/{username}/time-left` with

```json
{ "operation": "add", "seconds": 300 }
```

`operation` is `add`, `subtract` or `set`, mapping to the daemon's
`+`, `-` and `=` (`checkAndSetTimeLeft` in
`server/config/configprocessor.py`). The response is `200` with the
`status` resource. `add` and `subtract` are not idempotent, so a
client that retries after a network failure may grant time twice; see
"Later additions".

The two operations used by the NixOS test are compositions, not
endpoints: "forbid login" is
`PATCH .../config {"limits_per_day": {"1": 0, ..., "7": 0}}`, and an
exemption is `POST .../time-left {"operation": "add", "seconds": 300}`.

### Trying it

`docs/demo-vm.md` describes a NixOS virtual machine with the daemon,
`timekprw` and users to try them on.

### Web UI

`timekprw` serves a small single-page UI from `web/static/` at `/`
(plain HTML, CSS and JavaScript; no build step). It lists users with
their time left, edits a user's limits, allowed hours and options
(sending only the changed fields as one `PATCH`), adds or removes
time for today, and edits the daemon settings. The
token is entered once per browser tab.

### Later additions

- An `Idempotency-Key` header on the `POST` endpoint, with the
  backend replaying the stored response for a repeated key, so that
  a retried `add` cannot grant time twice.
- The GTK administration tool talking to this API (it still uses
  D-Bus only).

- `GET /api/v1/users/{username}/status/stream`: server-sent events
  with the `status` object every poll interval, so the UI can show a
  live countdown without polling the API. The backend would poll
  D-Bus at the daemon's `poll_time`.
- Batch reads (`GET /api/v1/users?include=config,status`) if the UI's
  overview page needs them.

## Mapping from `timekpra`

| `timekpra`                            | API                                                |
| ------------------------------------- | -------------------------------------------------- |
| `--userlist`                          | `GET /users`                                       |
| `--userinfo U`                        | `GET /users/U/config`                              |
| `--userinfort U`                      | `GET /users/U/status`                              |
| `--setalloweddays U '1;2;3'`          | `PATCH /users/U/config {"allowed_days": [1,2,3]}`  |
| `--setallowedhours U DAY '7;8[0-30]'` | `PUT /users/U/config/allowed-hours/DAY [...]`      |
| `--settimelimits U '7200;...'`        | `PATCH /users/U/config {"limits_per_day": {...}}`  |
| `--settimelimitweek U N`              | `PATCH /users/U/config {"limit_per_week": N}`      |
| `--settimelimitmonth U N`             | `PATCH /users/U/config {"limit_per_month": N}`     |
| `--settrackinactive U B`              | `PATCH /users/U/config {"track_inactive": B}`      |
| `--sethidetrayicon U B`               | `PATCH /users/U/config {"hide_tray_icon": B}`      |
| `--settimeleft U OP N`                | `POST /users/U/time-left {"operation", "seconds"}` |
| `--deletepolicy U`                    | `DELETE /users/U/policy`                           |
| `--grouplist`                         | `GET /groups`                                      |
| `--groupinfo G`                       | `GET /groups/G/config`                             |
| `--set* @G ...`                       | the user form, on `/groups/G/config`               |
| `--setoverrides @G 'A;B'`             | `PATCH /groups/G/config {"overrides": ["A","B"]}`  |
| `--deletepolicy @G`                   | `DELETE /groups/G/policy`                          |
| `--migratepolicies dry-run\|delete`   | `POST /policies/migrate {"dry_run": true\|false}`  |

## Sources

- CLI command list: `common/constants/constants.py`, `TK_USER_ADMIN_COMMANDS`.
- CLI argument parsing and output formatting: `client/admin/adminprocessor.py`.
- D-Bus admin methods and their signatures: `server/interface/dbus/daemon.py`.
- Validation rules (time-left operations, seven daily limits, hour map shape): `server/config/configprocessor.py`.
- User list derivation from config files: `server/config/userhelper.py`, `getSavedUserList`.
- Policies, their resolution and the group membership lookup: `server/config/policy.py`.
