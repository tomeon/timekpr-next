# NixOS integration test for timekpr.
#
# A single machine runs the demo configuration (../demo/module.nix):
# the upstream `services.timekpr` module with this flake's timekpr
# package, its web front end timekprw, a local user (alice), and a
# Kanidm identity server whose UNIX daemon provides a POSIX user
# (bob@idm.nixos.test).  For each user the test (timekpr.py) logs in
# over SSH (through PAM) and checks that timekpr terminates the session
# when the user has no screen time left, and leaves it alone once the
# user has been granted extra time.  The settings are made through
# timekpra talking D-Bus for one user and through timekpra talking to
# the web front end (socket activated, over its UNIX socket) for the
# other; the web API is also checked directly against what timekpra
# reports.
#
# `backend` selects how the machine is run: "vm" for a QEMU virtual
# machine (`nodes`), "container" for a systemd-nspawn container
# (`containers`), which starts faster and does not need KVM.
{
  timekpr,
  demoModule,
  backend ? "vm",
}: {
  pkgs,
  lib,
  ...
}: let
  settings = import ../demo/settings.nix;

  # Values the test script needs; see the top of timekpr.py.
  testConfig =
    settings
    // {
      bob = "${settings.bob}@${settings.idmDomain}";
      timekprPackage = "${timekpr}";
      # the same file the demo module hands to timekprw as a credential
      timekprwTokenFile = "${pkgs.writeText "timekprw-token" settings.timekprwToken}";
    };

  machine = {
    imports = [demoModule];

    virtualisation =
      {
        # Everything the test talks to lives on this one machine, so it
        # needs no network beyond loopback.
        vlans = [];
      }
      // lib.optionalAttrs (backend == "vm") {
        memorySize = 2048;
        cores = 2;
      };
  };
in
  {
    name = "timekpr";

    testScript =
      ''
        import json

        CONFIG = json.loads(${builtins.toJSON (builtins.toJSON testConfig)})
      ''
      + builtins.readFile ./timekpr.py;
  }
  // (
    if backend == "container"
    then {containers.machine = machine;}
    else {nodes.machine = machine;}
  )
