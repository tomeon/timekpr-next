# Guidelines for agents working on this repository

This is timekpr-next (a screen time manager) with a Nix flake bolted on.
Upstream code lives in `bin/`, `client/`, `common/`, `server/`,
`resource/` and `debian/`. Everything Nix-related was added on top:
`flake.nix`, `flake.lock`, `nix/`, `scripts/`, `.github/`, `.actrc`.

## Flake layout

- `flake.nix` uses flake-parts, the numtide devshell flake module, and
  the treefmt-nix flake module. Supported systems: x86_64-linux,
  aarch64-linux, aarch64-darwin. nixpkgs unstable has dropped
  x86_64-darwin, so do not add it back.
- `packages.timekpr` is nixpkgs' `timekpr` derivation with `src`
  overridden to this checkout (a fileset that excludes the Nix, script,
  CI, and `docs/` files so editing them does not rebuild the package).
  `packages.default` is the same derivation. The nixpkgs derivation
  hardcodes `version = "0.5.8"` inside a `rec` attribute set, so
  overriding `version` alone does not propagate into its generated
  `setup.py`.
- `nixosModules.demo` (`nix/demo/module.nix`) is the machine with
  timekpr, `timekprw`, a local user, a Kanidm domain user and the
  authorization test users; `nix/demo/settings.nix` holds the values
  the test also needs. `nixosConfigurations.demo` (and
  `demo-aarch64-linux`) is that module plus the QEMU VM profile
  (`nix/demo/vm.nix`, port forwards, console autologin); see
  `docs/demo-vm.md`. The module gets this flake's package through
  `inputs.self`, so it works from any host system.
- `checks.<system>.timekpr` is a `pkgs.testers.nixosTest` defined in
  `nix/tests/timekpr.nix` (the demo module plus VM sizing) and
  `nix/tests/timekpr.py` (test script). All machine setup lives in
  the module: what used to be imperative in the script (giving bob
  POSIX attributes with the kanidm CLI) is the oneshot
  `demo-kanidm-users.service`, which the test waits for.
  `checks.<system>.treefmt` comes from treefmt-nix.
- `legacyPackages.<system>.tests.timekpr-container` is the same test on
  the systemd-nspawn backend (`backend = "container"`). It is not a
  check because it needs a cgroup v2 host and a Nix daemon configured
  with `auto-allocate-uids`, the `uid-range` system feature, and the
  `auto-allocate-uids` and `cgroups` experimental features.
- The devshell (`nix develop`, `menu`) provides `act`, the treefmt
  wrapper, git, python3, and the helper scripts as commands.

## Web front end (`timekprw`)

`web/` holds the web application, packaged as `timekpr.web` like the
other top-level directories: `models.py` (Pydantic models),
`bridge.py` (translation onto `timekprAdminConnector`, the D-Bus client
`timekpra` uses), `app.py` (FastAPI routes), `timekprw.py` (entry
point, listeners, socket activation) and `static/` (the UI).
`common/utils/webapi.py` holds the daemon/JSON conversions in both
directions, shared with `client/interface/http/administration.py`, the
HTTP connector `timekpra --server URL` uses in place of the D-Bus one
(same method names and `(result, message[, payload])` results; its
scalar setters are generated from the field tables). `bin/timekprw`
is a launcher like the other three; `resource/server/systemd/` has its
`timekprw.service` and `timekprw.socket`. `docs/web-api.md` is the
API reference and is installed with the package.

- Every installed file is listed in `debian/install`; the nixpkgs
  derivation reads that file, installs `usr/share`, `usr/bin`, `etc`,
  `lib` and `var` entries itself and leaves Python modules to
  `setup.py`. A new Python module still needs a line there for the
  Debian package. Static files go to `usr/share/timekpr/web/`.
- The flake's `src` only contains git-tracked files, so `git add` new
  files before `nix build` or they are missing from the package.
- The launchers in `bin/` are shebang lines running the module, so the
  launcher's own path arrives as the first argument; `timekprw.py`
  drops it, as `adminprocessor.py` does for `timekpra`.
- `timekprAdminConnector.initTimekprConnection` retries through
  `GLib.timeout_add_seconds` unless `pTryOnce` is set; `timekprw` has
  no GLib main loop, so it always passes `pTryOnce=True` and
  reconnects on demand.
- `LIMITS_PER_WEEKDAYS` is positional against `ALLOWED_WEEKDAYS`
  (`server/user/userdata.py`), so the API's `limits_per_day` map is
  translated on both sides and the limits are re-sent whenever the
  allowed days change.
- `timekprw.service` runs as the static user `timekprw` (a member of
  `timekpr`, from `resource/server/sysusers.d/timekprw.conf`; the
  NixOS test declares it) rather than a `DynamicUser`: the daemon asks
  polkit, whose group rule resolves membership through NSS, which
  does not see a process-only `SupplementaryGroups=`.
- The web dependencies (FastAPI, uvicorn) are added to nixpkgs'
  derivation in `flake.nix` via `propagatedBuildInputs` and to
  `debian/control` as `Recommends`.
- The connector appends the daemon's reason to its access-denied
  message, so `bridge.py` matches that message by prefix.
- Listening is "bound socket in, web server on top": `--listen`
  accepts `HOST:PORT`, `unix:PATH` and `fd:N`, and sockets passed by
  systemd socket activation are picked up from `LISTEN_FDS`. UNIX
  socket connections need no token (uvicorn reports them with port
  `None`); TCP does. `timekprw.service` deliberately has no
  `After=timekpr.service` (that unit orders itself after
  `multi-user.target`, which made a cycle) and no `RuntimeDirectory`
  (it would delete the socket unit's socket on stop).
- The NixOS module makes `/etc/timekpr` a read-only store path, so
  the daemon cannot save daemon-wide settings there (it writes
  `timekpr.conf` and `timekpr.conf.prev` in place); the test expects
  a `500` from `PATCH /api/v1/config` for that reason.
- `checks.<system>.web` runs the pytest suite in `nix/tests/web/`
  (no VM, seconds): the API through FastAPI's `TestClient`, the
  conversions, and the listeners, socket activation and HTTP
  connector against a real uvicorn started through `timekprw.main()`
  with a fake connector (`fake.py`; `Bridge(connector)` takes any
  object with the connector's method names, and `main()` takes a
  `bridge`; `helpers.py` starts that server). `serve.py` presents
  activation descriptors itself, since `LISTEN_PID` cannot be set
  from a `preexec_fn`.
- `checks.<system>.web-ui` runs `test_ui.py` from the same suite: the
  UI in nixpkgs' Playwright Chromium (`playwright-driver.browsers-chromium`
  via `PLAYWRIGHT_BROWSERS_PATH`, launched with `--no-sandbox` because
  the Nix sandbox has no user namespaces) against `timekprw` on the
  fake connector, checking the daemon side through the HTTP connector.
  It is a separate check so the API tests need no browser; locally,
  `TIMEKPRW_TEST_CHROMIUM=/path/to/chrome` points Playwright at
  another Chromium. OCR in the NixOS test was rejected: the UI has a
  DOM to assert on, and OCR would need a desktop session in the VM
  and fuzzy text matching.
- The NixOS test runs `timekprw` socket activated (the package's
  socket unit plus a TCP `listenStreams` drop-in), drives one user
  through `timekpra` over D-Bus and the other through `timekpra
--server unix://...`, compares `--userlist`/`--userinfo` output of
  all three transports, and checks the API directly with `curl`.
- The daemon answers failures with `-1` and a message in its own
  locale; `bridge.py` recognizes the failure messages in every
  installed locale (`daemon_failure_texts`) rather than assuming the
  two processes share one. Requests on TCP must carry a `Host` header
  naming an address `timekprw` serves (DNS rebinding); trust of UNIX
  sockets is decided per socket at bind time and fails closed.

## Conventions

- Run `nix fmt` after every change and fix anything a formatter reports
  but cannot fix itself. treefmt runs alejandra (Nix), prettier
  (Markdown, JSON, YAML), xmllint (XML and the SVG icons), shellcheck
  and shfmt (shell), and ruff-check and ruff-format (Python).
- Everything with a recognised extension is formatted, timekpr's own
  sources included. The helper scripts have none, so every new shell or
  Python script must also be added to the `shellScripts` or
  `pythonScripts` list in `flake.nix`.
- `ruff.toml` turns off the handful of ruff's default rules that
  describe a style timekpr does not follow (`BLE001` and `S110` for the
  deliberate catch-alls, `DTZ` for its naive local-time arithmetic).
  Prefer fixing a finding over adding to that list; a one-off
  intentional violation gets a `# noqa: RULE (why)` instead.
- Shell scripts: a script that is POSIX sh compatible uses
  `#!/bin/sh`; anything needing bash features uses `#!/usr/bin/env bash`
  and bash idioms throughout (`[[ ]]`, arrays, `(( ))`). Write
  conditionals as `if cmd; then ...; fi` and `if ! cmd; then ...; fi`
  rather than `cmd && thing` or `cmd || thing`, unless the chaining is
  the point (for example `(umask 077 && ...)`).
- Prefer an existing library function over hand-rolled code. Example:
  D-Bus object path escaping of user names uses
  `Gio.dbus_escape_object_path`, which is lossless, instead of
  stripping characters.
- Keep the NixOS test's Python in its own file. `timekpr.nix` passes
  values to it by prepending a `CONFIG` dict (JSON) to the script; the
  driver injects `machine` and `subtest`. The test script carries
  `# ruff: noqa: F821` for those names.
- Every task ends with the relevant success commands exiting 0, and
  with the work committed and pushed to the designated branch.

## Verification commands

All of these must exit 0:

```
nix flake show
nix develop -c menu
nix fmt
nix build .#timekpr
nix build .#            # same output path as .#timekpr
nix flake check         # or scripts/flake-check without KVM
act                     # or scripts/act-sandboxed in a restricted sandbox
```

`nix flake check` and `act` each take roughly 10 minutes without KVM,
because the NixOS test then runs under QEMU software emulation. Run
them detached and follow the log rather than waiting on a foreground
command with a timeout. `nix flake check` reuses an already-built test
derivation when nothing in the package source or test changed, so a
fast pass may not have re-executed the VM; `scripts/run-nixos-test`
always re-executes it.

## Helper scripts (`scripts/`, also devshell commands)

They exist because the development sandbox has these restrictions;
each one degrades to the plain command on an ordinary machine.

| Restriction                                                                                                                                                                           | Tool                                                                                                                 |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Nix is installed but not on `PATH` (it lives in `/nix/var/nix/profiles/default/bin`)                                                                                                  | `scripts/lib.sh` `ensure_nix_on_path`                                                                                |
| GitHub's API and tarball endpoints return 403 for repositories outside the session, while anonymous `git` access works; so `github:` flake inputs cannot be fetched or updated by Nix | `scripts/flake-inputs-via-git` (fetches locked inputs over git; `--update` replaces `nix flake update`)              |
| No `/dev/kvm`, so Nix refuses the NixOS test for lack of the `kvm` feature                                                                                                            | `scripts/flake-check` (adds `--option extra-system-features kvm`)                                                    |
| Iterating on the NixOS test inside the sandbox is slow and opaque                                                                                                                     | `scripts/run-nixos-test` (builds the driver, runs it outside the sandbox, logs under `$XDG_CACHE_HOME/timekpr-next`) |
| Internet only via a loopback HTTPS proxy (`HTTPS_PROXY`) with a private CA (`SSL_CERT_FILE` etc.); no Docker daemon running; act's job container cannot reach either                  | `scripts/act-sandboxed` (host networking, CA mount, signed `file://` cache of the flake inputs, starts `dockerd`)    |
| Looking up the latest release of a GitHub project                                                                                                                                     | `scripts/github-latest-tags`                                                                                         |

Other facts about the sandbox worth knowing before trying something:

- The kernel has no IPv6, so services in tests bind `127.0.0.1`, not
  `[::]`, and `networking.hosts` entries use IPv4 only.
- The host uses the legacy cgroup v1 hierarchy, so systemd-nspawn
  refuses to start containers. The container test backend cannot be
  exercised here at all.
- Nix runs in single-user mode as root. `--option` settings on the
  command line are honoured.
- The proxy's port changes between sessions; always read it from the
  environment.

## Things learned about timekpr and the test

- timekpr enforces limits by terminating logind sessions, not by
  blocking PAM. It polls every 3 seconds and terminates an over-limit
  session after a 15 second countdown (`TIMEKPR_TERMINATION_TIME`), so
  "login fails" in the test means the SSH session is killed within about
  30 seconds, and "login succeeds" means it survives a 45 second hold.
- By default it only tracks `x11;wayland;mir` sessions and explicitly
  ignores `tty`. SSH logins are `tty` sessions, so the test ships a
  modified `/etc/timekpr/timekpr.conf` (via `environment.etc.timekpr`)
  that tracks `tty`.
- "Forbid login" is expressed as `timekpra --settimelimits USER
'0;0;0;0;0;0;0'`; an exemption is `timekpra --settimeleft USER + 300`.
  A user must have a config file before `timekpra` accepts settings;
  the daemon creates one on the user's first login, which is why the
  test logs each user in once unrestricted first.
- The daemon's log is `/var/log/timekpr.log` and is flushed lazily;
  wait for lines rather than asserting on them immediately, and stop
  the service to flush it when diagnosing a failure.
- Two upstream bugs were fixed for the test to pass: config comments
  containing `:` or `=` crash configparser on Python 3.14, and per-user
  D-Bus object paths were built by stripping characters from the user
  name, which broke domain users such as `bob@idm.nixos.test`.
- Access to the daemon's D-Bus interface is decided by the daemon, not
  the bus policy (which now lets anyone send). Methods on the two
  admin interfaces are declared with `timekprAuthorizedMethod` from
  `server/interface/dbus/polkit.py`, which asks polkit for one of the
  actions in `resource/server/polkit/com.timekpr.server.policy` (with
  `user` and `method` details for rules) and replies asynchronously
  once polkit answers, so an authentication prompt does not block the
  main loop. Every method on those interfaces must use it. The
  per-user interfaces only accept calls from that user or root. Root
  is always authorized (polkit does the same), which is why `timekpra`
  as root in the test needs no rule. `50-timekpr.rules` reproduces
  the old `timekpr` group grant; site rules sorting earlier win.
  `timekpr.pkla` says the same for polkit 0.105 and earlier (Ubuntu
  22.04, Debian 11), which ignore `.rules` files; polkit 0.106 and
  later ignore `.pkla` files, so both ship. polkit remembers an
  `auth_admin_keep` authentication per action id, which is why all
  read-only methods share one action and the configuring actions keep
  too: the admin GUI reads on open and applies a page as several calls.
- `timekpra` always exits 0; the test detects refused commands by the
  "access denied" text in its output and by checking that nothing
  changed. Polkit refuses non-root callers that have no agent to
  authenticate with, so denial is immediate in the test.
- `-h` and `--help` are answered by all three commands before any other
  work: the check is the first thing each entry script does, ahead of its
  own timekpr imports, so no self-running check, configuration, log file,
  D-Bus, GTK or daemon is involved and help works for anyone who may
  execute the command, in any state of the system (`env -i`, no bus, no
  `/etc/timekpr`). The texts live in `common/utils/cmdhelp.py`, which
  imports nothing until help is actually printed; it then pulls in
  `constants` (and with it the `dbus` python module, for the version and
  timekpr's own command list) and nothing else. Keep it that way when
  adding start-up work, and keep the check ahead of the imports: loading
  GTK for a help message is what made `timeout 3s timekprc --help` fail.
  Every other `timekpra` command still needs the daemon; the connector
  connects to the bus lazily, so a missing bus is reported rather than
  raised.
- The nixpkgs derivation runs `substituteInPlace --replace-fail` on
  every `.policy` file, which fails on one not mentioning
  `/usr/bin/timekpr`; the flake narrows that glob to `*.pkexec.policy`.
- Kanidm: provisioning the `idm_admin` password requires
  `kanidm_1_11.withSecretProvisioning`; `services.kanidm.provision`
  cannot set POSIX attributes or passwords, so the test does that with
  the CLI, which only reads passwords from a terminal (the
  `answer-password` pexpect helper handles that). Kanidm enforces a
  minimum UNIX password quality. The UNIX daemon exposes users under
  their SPN (`bob@idm.nixos.test`).

## Communication

- Be polite but not fawning; get to the point.
- Do not put words in the user's mouth. When something they said
  seems wrong, consider first that the misunderstanding may be yours.
- Apply the principle of charity; pick no nits.
- Provide citations and links for sources.
