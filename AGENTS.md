# Guidelines for agents working on this repository

This is timekpr-next (a screen time manager) with a Nix flake bolted on.
Upstream code lives in `bin/`, `client/`, `common/`, `server/`,
`resource/` and `debian/`.  Everything Nix-related was added on top:
`flake.nix`, `flake.lock`, `nix/`, `scripts/`, `.github/`, `.actrc`.

## Flake layout

- `flake.nix` uses flake-parts, the numtide devshell flake module, and
  the treefmt-nix flake module.  Supported systems: x86_64-linux,
  aarch64-linux, aarch64-darwin.  nixpkgs unstable has dropped
  x86_64-darwin, so do not add it back.
- `packages.timekpr` is nixpkgs' `timekpr` derivation with `src`
  overridden to this checkout (a fileset that excludes the Nix, script,
  CI, and `docs/` files so editing them does not rebuild the package).
  `packages.default` is the same derivation.  The nixpkgs derivation
  hardcodes `version = "0.5.8"` inside a `rec` attribute set, so
  overriding `version` alone does not propagate into its generated
  `setup.py`.
- `checks.<system>.timekpr` is a `pkgs.testers.nixosTest` defined in
  `nix/tests/timekpr.nix` (machine configuration) and
  `nix/tests/timekpr.py` (test script).  `checks.<system>.treefmt`
  comes from treefmt-nix.
- `legacyPackages.<system>.tests.timekpr-container` is the same test on
  the systemd-nspawn backend (`backend = "container"`).  It is not a
  check because it needs a cgroup v2 host and a Nix daemon configured
  with `auto-allocate-uids`, the `uid-range` system feature, and the
  `auto-allocate-uids` and `cgroups` experimental features.
- The devshell (`nix develop`, `menu`) provides `act`, the treefmt
  wrapper, git, python3, and the helper scripts as commands.

## Conventions

- Run `nix fmt` after every change to Nix, shell, or Python files and
  fix anything a formatter reports but cannot fix itself.  treefmt runs
  alejandra (Nix), shellcheck and shfmt (the listed shell scripts), and
  ruff-check and ruff-format (the listed Python files).
- The Python formatters must only ever apply to Python written for the
  flake (`nix/tests/timekpr.py`, `scripts/flake-inputs-via-git`).
  Never reformat timekpr's own Python sources.  The scripts have no
  file extensions, so every new shell or Python script must be added to
  the `shellScripts` or `pythonScripts` list in `flake.nix`.
- Shell scripts: a script that is POSIX sh compatible uses
  `#!/bin/sh`; anything needing bash features uses `#!/usr/bin/env bash`
  and bash idioms throughout (`[[ ]]`, arrays, `(( ))`).  Write
  conditionals as `if cmd; then ...; fi` and `if ! cmd; then ...; fi`
  rather than `cmd && thing` or `cmd || thing`, unless the chaining is
  the point (for example `(umask 077 && ...)`).
- Prefer an existing library function over hand-rolled code.  Example:
  D-Bus object path escaping of user names uses
  `Gio.dbus_escape_object_path`, which is lossless, instead of
  stripping characters.
- Keep the NixOS test's Python in its own file.  `timekpr.nix` passes
  values to it by prepending a `CONFIG` dict (JSON) to the script; the
  driver injects `machine` and `subtest`.  The test script carries
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
because the NixOS test then runs under QEMU software emulation.  Run
them detached and follow the log rather than waiting on a foreground
command with a timeout.  `nix flake check` reuses an already-built test
derivation when nothing in the package source or test changed, so a
fast pass may not have re-executed the VM; `scripts/run-nixos-test`
always re-executes it.

## Helper scripts (`scripts/`, also devshell commands)

They exist because the development sandbox has these restrictions;
each one degrades to the plain command on an ordinary machine.

| Restriction | Tool |
| --- | --- |
| Nix is installed but not on `PATH` (it lives in `/nix/var/nix/profiles/default/bin`) | `scripts/lib.sh` `ensure_nix_on_path` |
| GitHub's API and tarball endpoints return 403 for repositories outside the session, while anonymous `git` access works; so `github:` flake inputs cannot be fetched or updated by Nix | `scripts/flake-inputs-via-git` (fetches locked inputs over git; `--update` replaces `nix flake update`) |
| No `/dev/kvm`, so Nix refuses the NixOS test for lack of the `kvm` feature | `scripts/flake-check` (adds `--option extra-system-features kvm`) |
| Iterating on the NixOS test inside the sandbox is slow and opaque | `scripts/run-nixos-test` (builds the driver, runs it outside the sandbox, logs under `$XDG_CACHE_HOME/timekpr-next`) |
| Internet only via a loopback HTTPS proxy (`HTTPS_PROXY`) with a private CA (`SSL_CERT_FILE` etc.); no Docker daemon running; act's job container cannot reach either | `scripts/act-sandboxed` (host networking, CA mount, signed `file://` cache of the flake inputs, starts `dockerd`) |
| Looking up the latest release of a GitHub project | `scripts/github-latest-tags` |

Other facts about the sandbox worth knowing before trying something:

- The kernel has no IPv6, so services in tests bind `127.0.0.1`, not
  `[::]`, and `networking.hosts` entries use IPv4 only.
- The host uses the legacy cgroup v1 hierarchy, so systemd-nspawn
  refuses to start containers.  The container test backend cannot be
  exercised here at all.
- Nix runs in single-user mode as root.  `--option` settings on the
  command line are honoured.
- The proxy's port changes between sessions; always read it from the
  environment.

## Things learned about timekpr and the test

- timekpr enforces limits by terminating logind sessions, not by
  blocking PAM.  It polls every 3 seconds and terminates an over-limit
  session after a 15 second countdown (`TIMEKPR_TERMINATION_TIME`), so
  "login fails" in the test means the SSH session is killed within about
  30 seconds, and "login succeeds" means it survives a 45 second hold.
- By default it only tracks `x11;wayland;mir` sessions and explicitly
  ignores `tty`.  SSH logins are `tty` sessions, so the test ships a
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
- Kanidm: provisioning the `idm_admin` password requires
  `kanidm_1_11.withSecretProvisioning`; `services.kanidm.provision`
  cannot set POSIX attributes or passwords, so the test does that with
  the CLI, which only reads passwords from a terminal (the
  `answer-password` pexpect helper handles that).  Kanidm enforces a
  minimum UNIX password quality.  The UNIX daemon exposes users under
  their SPN (`bob@idm.nixos.test`).

## Communication

- Be polite but not fawning; get to the point.
- Do not put words in the user's mouth.  When something they said
  seems wrong, consider first that the misunderstanding may be yours.
- Apply the principle of charity; pick no nits.
- Provide citations and links for sources.
