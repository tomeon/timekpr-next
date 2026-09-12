# The demo machine

`nixosConfigurations.demo` is a NixOS virtual machine running timekpr,
its web front end `timekprw`, and users to try them on.  The same
machine, as the flake's `nixosModules.demo` (`nix/demo/module.nix`),
is what the NixOS test runs; the demo adds only the QEMU VM profile
(`nix/demo/vm.nix`).

## Running it

```
nix build .#nixosConfigurations.demo.config.system.build.vm
./result/bin/run-timekpr-demo-vm
```

The VM boots on the terminal (no graphics window) and logs root in on
the console.  It forwards two ports to the host's loopback:

| Host                    | Guest                       |
| ----------------------- | --------------------------- |
| `http://localhost:8463` | the web UI and API          |
| `ssh -p 2222 …@localhost` | SSH, password authentication |

Open <http://localhost:8463/> in a browser and enter the token
`timekprw-test-token` (the "Token" page; it is kept in the browser tab
only).  The API is at `http://localhost:8463/api/v1/`, with its
interactive documentation at `/api/v1/docs`.  To reach the VM's network
from another address or port, add QEMU user-network options when
starting it, for example to serve the UI on port 9000 of every host
interface as well:

```
QEMU_NET_OPTS='hostfwd=tcp:0.0.0.0:9000-:8463' ./result/bin/run-timekpr-demo-vm
```

(`QEMU_NET_OPTS` is appended to the `-netdev user` options; see
`virtualisation.forwardPorts` in `nix/demo/vm.nix` for the defaults.)
The web front end accepts the `Host` headers `localhost`, `127.0.0.1`
and `::1`; for another name add a drop-in with
`Environment=TIMEKPRW_ALLOWED_HOSTS=name` to `timekprw.service`.
`QEMU_OPTS` passes further options to QEMU, and the VM keeps its state
in `timekpr-demo.qcow2` in the current directory; delete that file for
a fresh machine.

The build takes a while the first time (it includes a Kanidm server);
`nix build` needs a Linux host with `/dev/kvm` for the VM to run at a
usable speed, but it runs without KVM too.  On an aarch64 host use
`nixosConfigurations.demo-aarch64-linux`.

## What is on the machine

Users, all with password authentication over SSH:

| User                  | Password               | Role                                                        |
| --------------------- | ---------------------- | ----------------------------------------------------------- |
| `alice`               | `alice-password`       | a local user whose screen time is managed                   |
| `bob@idm.nixos.test`  | `Nk7rP2xW9qL4mZ8vT3bH` | a domain user from the Kanidm server on the machine         |
| `carol`               | none (`runuser` from root) | no privileges: `timekpra` is refused by polkit          |
| `dave`                | none                   | member of the `timekpr` group: may administer everything    |
| `erin`                | none                   | a polkit site rule lets her grant time to alice only        |
| `root`                | none (console autologin) | `timekpra`, the daemon's log in `/var/log/timekpr.log` |

timekpr tracks SSH (`tty`) sessions on this machine, so a limit can be
watched taking effect: forbid alice's screen time from the UI (or with
`timekpra --settimelimits alice '0;0;0;0;0;0;0'` on the console), log
in as alice over SSH, and the session is terminated within about half
a minute; grant time (`timekpra --settimeleft alice + 300`) and the
next login stays.  `timekpra --server unix:///run/timekprw/timekprw.sock
--userinfo alice` talks to the web front end instead of D-Bus and
prints the same thing.

Kanidm serves `https://idm.nixos.test` on the machine's loopback with
a self-signed certificate; `idm_admin`'s password is
`idm-admin-password`.  bob's POSIX attributes and password are set by
the oneshot `demo-kanidm-users.service` after the server is up.

Everything here is demo data, fixed in `nix/demo/settings.nix`; the
machine is not meant to be reachable from anything but the host it
runs on.
