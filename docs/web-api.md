# timekpr web API

`timekprw` is a thin HTTP front end for the timekpr daemon.  Every
endpoint translates directly into one or more calls on the daemon's
D-Bus interfaces (`com.timekpr.server.user.admin` and
`com.timekpr.server.admin`, see `server/interface/dbus/daemon.py`),
made through the same `timekprAdminConnector` that `timekpra` and the
GTK administration tool use.  The backend keeps no state of its own;
the daemon's configuration files remain the source of truth.

The implementation lives in `web/`: `models.py` (the JSON shapes),
`bridge.py` (translation to and from the daemon), `app.py` (FastAPI
routes) and `timekprw.py` (the executable).  The web UI in
`web/static/` is served by the same process.  An OpenAPI document is
available at `/api/v1/openapi.json` and an interactive one at
`/api/v1/docs`.

## Running

`timekprw` listens on `127.0.0.1:8463` by default and is started by the
`timekprw.service` unit as an unprivileged dynamic user in the
`timekpr` group, which is what the daemon's D-Bus policy requires for
the administration interfaces.  Options (`--host`, `--port`,
`--token-file`, `--static-dir`, `--root-path`, `--no-auth`) can also be
given as environment variables `TIMEKPRW_<OPTION>`, for example through
`/etc/timekpr/timekprw.env`, which the unit reads if it exists.

Every endpoint except `/health` requires a bearer token
(`Authorization: Bearer <token>`).  The token is read from, in order:
the file named by `--token-file`, the systemd credential `token` (a
drop-in with `LoadCredential=token:/path/to/file`), or
`/etc/timekpr/timekprw.token`.  Without a token file the service
refuses to start unless `--no-auth` is given, which is only appropriate
behind a reverse proxy that authenticates, or on loopback.  TLS is
left to a reverse proxy; `--root-path` is the prefix such a proxy
strips.

## Conventions

- Base path `/api/v1`.  Request and response bodies are JSON.
- Usernames appear as a single percent-encoded path segment.  Domain
  users such as `bob@idm.nixos.test` are addressed as
  `/users/bob%40idm.nixos.test`.  The backend must pass the decoded
  name to D-Bus unchanged.
- All durations are integer seconds, timekpr's native unit.  Weekdays
  are ISO numbers `1` (Monday) to `7` (Sunday), as timekpr uses them.
  Hours are `0`-`23`.  Booleans are JSON booleans.
- Idempotent updates use `PATCH` (partial) or `PUT` (full replacement
  of a list).  Operations that are not idempotent, namely adding or
  subtracting time, use `POST`.
- A successful write returns the updated resource, so the UI never
  needs a follow-up `GET`.
- Errors use RFC 9457 `application/problem+json`
  (<https://www.rfc-editor.org/rfc/rfc9457>):
  `{"type", "title", "status", "detail", "errors": [{"field", "message"}]}`.
  `errors` and `applied` are omitted when empty.  A user-scoped request
  for a user the daemon has no configuration for is `404`; a value the
  daemon refuses is `400` with its message as `detail`; `500` means
  the daemon accepted the request but failed to apply it (its log,
  `/var/log/timekpr.log`, has the reason; on NixOS `/etc/timekpr` is
  a read-only store path, so the daemon-wide settings cannot be
  changed there); `503` means the daemon (or the system bus) is not
  reachable and `502` that the daemon's D-Bus policy refused
  `timekprw`.  `401` is a missing or wrong token.
- Authentication is a bearer token on every endpoint except
  `/health`, see "Running" above.  The backend process itself must
  run as root or as a member of the `timekpr` group, because that is
  who the daemon's D-Bus policy admits to the admin interfaces
  (`server/interface/dbus/daemon.py`, comment above `setAllowedDays`).
- `PATCH` on a config resource is applied as a sequence of D-Bus
  setters, one per field, in a fixed order.  The backend validates
  the whole body first.  If a setter still fails part-way, the
  response is `400` with `errors` naming the failed field and
  `applied` listing the fields that were written before it.

## Endpoints

### Service

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/health` | Daemon reachability and versions. `200 {"daemon": "ok", "timekpr_version": "0.5.8"}` or `503 {"daemon": "unreachable"}`. |
| `GET` | `/api/v1/config` | Daemon-wide configuration (`getTimekprConfiguration`). No `timekpra` equivalent; the GTK admin UI uses this call. |
| `PATCH` | `/api/v1/config` | Update any subset of the fields below. Each maps to one `setTimekpr*` setter. |

Fields of `/api/v1/config` (names follow `TIMEKPR_*` keys returned by the daemon):

| Field | Type | D-Bus setter |
| --- | --- | --- |
| `log_level` | int (1-3) | `setTimekprLogLevel` |
| `poll_time` | seconds | `setTimekprPollTime` |
| `save_time` | seconds | `setTimekprSaveTime` |
| `termination_time` | seconds | `setTimekprTerminationTime` |
| `final_warning_time` | seconds | `setTimekprFinalWarningTime` |
| `final_notification_time` | seconds | `setTimekprFinalNotificationTime` |
| `session_types_tracked` | list of strings, e.g. `["x11","wayland","mir","tty"]` | `setTimekprSessionsCtrl` |
| `session_types_excluded` | list of strings | `setTimekprSessionsExcl` |
| `users_excluded` | list of usernames | `setTimekprUsersExcl` |
| `playtime_enabled` | bool | `setTimekprPlayTimeEnabled` |
| `playtime_enhanced_activity_monitor` | bool | `setTimekprPlayTimeEnhancedActivityMonitorEnabled` |

### Users

| Method | Path | `timekpra` | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/users` | `--userlist` | Users that have a timekpr configuration. Returns `[{"username", "full_name"}]`. Query `?include=status` adds each user's `status` object (one extra D-Bus call per user). |
| `GET` | `/api/v1/users/{username}` | `--userinfo` + `--userinfort` | Full view: `{"username", "config": {...}, "status": {...}}` (`getUserInformation(name, "F")`). |
| `GET` | `/api/v1/users/{username}/config` | `--userinfo` | Saved configuration only (`"S"`). |
| `PATCH` | `/api/v1/users/{username}/config` | all `--set*` except time left | Partial update, see field table below. |
| `GET` | `/api/v1/users/{username}/status` | `--userinfort` | Realtime counters (`"R"`). |
| `PUT` | `/api/v1/users/{username}/config/allowed-hours/{day}` | `--setallowedhours` | Replace the allowed hours for one weekday, or for every weekday when `{day}` is `all`. |
| `PUT` | `/api/v1/users/{username}/config/playtime/activities` | `--setplaytimeactivities` | Replace the PlayTime activity list. |
| `POST` | `/api/v1/users/{username}/time-left` | `--settimeleft` | Add, subtract or set today's remaining time. |
| `POST` | `/api/v1/users/{username}/playtime-left` | `--setplaytimeleft` | Same for PlayTime. |

There is deliberately no `POST /users` or `DELETE /users/{username}`.
`timekpra` cannot create or remove a user: the daemon writes a user's
configuration on that user's first tracked login, and `timekpra`
refuses settings for a user without one.  If those operations turn
out to be needed they require daemon changes first, and the API would
gain `POST /api/v1/users {"username"}` returning `201` and
`DELETE /api/v1/users/{username}` returning `204`.

#### `config` resource

`GET /api/v1/users/{username}/config` returns, and `PATCH` accepts any
subset of:

| Field | Type | D-Bus setter | Notes |
| --- | --- | --- | --- |
| `allowed_days` | list of weekdays, e.g. `[1,2,3,4,5]` | `setAllowedDays` | |
| `limits_per_day` | object weekday → seconds, `{"1": 7200, ..., "7": 10800}` | `setTimeLimitForDays` | The daemon stores limits positionally against `allowed_days` (`server/user/userdata.py`), so `GET` lists only allowed days, keys for other days are ignored, missing days keep their current value, and a change of `allowed_days` re-sends the limits aligned with the new days. Values are clamped to 86400 by the daemon. |
| `allowed_hours` | object weekday → list of hour entries (below) | `setAllowedHours`, once per day given | Same shape as the `PUT` sub-resource; `PATCH` is for editing several days in one request. |
| `limit_per_week` | seconds | `setTimeLimitForWeek` | |
| `limit_per_month` | seconds | `setTimeLimitForMonth` | |
| `track_inactive` | bool | `setTrackInactive` | |
| `hide_tray_icon` | bool | `setHideTrayIcon` | |
| `lockout` | `{"type": "lock"｜"suspend"｜"suspendwake"｜"terminate"｜"kill"｜"shutdown", "wake_from": hour, "wake_to": hour}` | `setLockoutType` | `wake_from`/`wake_to` are required with `suspendwake`, rejected with other types, and `null` in responses for other types; the CLI form is `suspendwake;7;18`. |
| `playtime.enabled` | bool | `setPlayTimeEnabled` | |
| `playtime.limit_override` | bool | `setPlayTimeLimitOverride` | |
| `playtime.allow_unaccounted_intervals` | bool | `setPlayTimeUnaccountedIntervalsEnabled` | |
| `playtime.allowed_days` | list of weekdays | `setPlayTimeAllowedDays` | |
| `playtime.limits_per_day` | object weekday → seconds | `setPlayTimeLimitsForDays` | Positional against `playtime.allowed_days`, handled like `limits_per_day`. |
| `playtime.activities` | list of `{"process", "description"}` | `setPlayTimeActivities` | CLI form `csgo_linux[CS: GO]`; `description` may be empty. |

An hour entry is
`{"hour": 11, "start_minute": 0, "end_minute": 30, "unaccounted": false}`.
`start_minute` defaults to `0`, `end_minute` to `60`, `unaccounted`
to `false`, so the CLI string `7;8;11[00-30];!14` becomes

```json
[{"hour": 7}, {"hour": 8},
 {"hour": 11, "start_minute": 0, "end_minute": 30},
 {"hour": 14, "unaccounted": true}]
```

This replaces the `;`/`[]`/`!` mini-language with structure the UI
can render directly; the backend converts to the daemon's
`{hour: {STARTMIN, ENDMIN, UACC}}` map.

Example `GET /api/v1/users/alice/config`:

```json
{
  "allowed_days": [1, 2, 3, 4, 5, 6, 7],
  "limits_per_day": {"1": 7200, "2": 7200, "3": 7200, "4": 7200, "5": 7200, "6": 10800, "7": 10800},
  "allowed_hours": {"1": [{"hour": 7}, {"hour": 8}], "2": [], "3": [], "4": [], "5": [], "6": [], "7": []},
  "limit_per_week": 50000,
  "limit_per_month": 200000,
  "track_inactive": false,
  "hide_tray_icon": false,
  "lockout": {"type": "terminate", "wake_from": null, "wake_to": null},
  "playtime": {
    "enabled": false,
    "limit_override": false,
    "allow_unaccounted_intervals": false,
    "allowed_days": [1, 2, 3, 4, 5, 6, 7],
    "limits_per_day": {"1": 1800, "2": 1800, "3": 1800, "4": 1800, "5": 1800, "6": 3600, "7": 3600},
    "activities": [{"process": "csgo_linux", "description": "CS: GO"}]
  }
}
```

#### `status` resource

Realtime values exist only while the daemon is tracking a session of
the user; otherwise the `actual_*` fields are `null` and only the
counters persisted in the user's control file are populated.  Field
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
  "time_inactive_session": 60,
  "playtime_spent_day": 0,
  "playtime_left_day": 1800,
  "playtime_active_activity_count": 0
}
```

#### Time-left actions

`POST /api/v1/users/{username}/time-left` with

```json
{"operation": "add", "seconds": 300}
```

`operation` is `add`, `subtract` or `set`, mapping to the daemon's
`+`, `-` and `=` (`checkAndSetTimeLeft` in
`server/config/configprocessor.py`).  The response is `200` with the
`status` resource.  Because `add` and `subtract` are not idempotent, a
client that retries after a network failure should send an
`Idempotency-Key` header and the backend should replay the stored
response for a repeated key.  `playtime-left` is identical and maps to
`setPlayTimeLeft`.

The two operations used by the NixOS test are compositions, not
endpoints: "forbid login" is
`PATCH .../config {"limits_per_day": {"1": 0, ..., "7": 0}}`, and an
exemption is `POST .../time-left {"operation": "add", "seconds": 300}`.

### Web UI

`timekprw` serves a small single-page UI from `web/static/` at `/`
(plain HTML, CSS and JavaScript; no build step).  It lists users with
their time left, edits a user's limits, allowed hours, lockout and
PlayTime settings (sending only the changed fields as one `PATCH`),
adds or removes time for today, and edits the daemon settings.  The
token is entered once per browser tab.

### Later additions

- `timekpra` talking to this API instead of D-Bus, selected by a
  `--server URL` option, for remote administration from the CLI.

- `GET /api/v1/users/{username}/status/stream`: server-sent events
  with the `status` object every poll interval, so the UI can show a
  live countdown without polling the API.  The backend would poll
  D-Bus at the daemon's `poll_time`.
- Batch reads (`GET /api/v1/users?include=config,status`) if the UI's
  overview page needs them.

## Mapping from `timekpra`

| `timekpra` | API |
| --- | --- |
| `--userlist` | `GET /users` |
| `--userinfo U` | `GET /users/U/config` |
| `--userinfort U` | `GET /users/U/status` |
| `--setalloweddays U '1;2;3'` | `PATCH /users/U/config {"allowed_days": [1,2,3]}` |
| `--setallowedhours U DAY '7;8[0-30]'` | `PUT /users/U/config/allowed-hours/DAY [...]` |
| `--settimelimits U '7200;...'` | `PATCH /users/U/config {"limits_per_day": {...}}` |
| `--settimelimitweek U N` | `PATCH /users/U/config {"limit_per_week": N}` |
| `--settimelimitmonth U N` | `PATCH /users/U/config {"limit_per_month": N}` |
| `--settrackinactive U B` | `PATCH /users/U/config {"track_inactive": B}` |
| `--sethidetrayicon U B` | `PATCH /users/U/config {"hide_tray_icon": B}` |
| `--setlockouttype U T[;F;T]` | `PATCH /users/U/config {"lockout": {...}}` |
| `--settimeleft U OP N` | `POST /users/U/time-left {"operation", "seconds"}` |
| `--setplaytimeenabled U B` | `PATCH /users/U/config {"playtime": {"enabled": B}}` |
| `--setplaytimelimitoverride U B` | `PATCH /users/U/config {"playtime": {"limit_override": B}}` |
| `--setplaytimeunaccountedintervalsflag U B` | `PATCH /users/U/config {"playtime": {"allow_unaccounted_intervals": B}}` |
| `--setplaytimealloweddays U '1;2'` | `PATCH /users/U/config {"playtime": {"allowed_days": [1,2]}}` |
| `--setplaytimelimits U '1800;...'` | `PATCH /users/U/config {"playtime": {"limits_per_day": {...}}}` |
| `--setplaytimeactivities U 'p[desc];...'` | `PUT /users/U/config/playtime/activities [...]` |
| `--setplaytimeleft U OP N` | `POST /users/U/playtime-left {"operation", "seconds"}` |

## Sources

- CLI command list: `common/constants/constants.py`, `TK_USER_ADMIN_COMMANDS`.
- CLI argument parsing and output formatting: `client/admin/adminprocessor.py`.
- D-Bus admin methods and their signatures: `server/interface/dbus/daemon.py`.
- Validation rules (lockout types, time-left operations, seven daily limits, hour map shape): `server/config/configprocessor.py`.
- User list derivation from config files: `server/config/userhelper.py`, `getSavedUserList`.
