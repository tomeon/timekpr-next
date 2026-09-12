# The demo machine as a QEMU virtual machine: `nixosConfigurations.demo`.
# See docs/demo-vm.md for how to run it.
{
  lib,
  modulesPath,
  ...
}: let
  settings = import ./settings.nix;
in {
  imports = ["${modulesPath}/virtualisation/qemu-vm.nix"];

  networking.hostName = "timekpr-demo";
  system.stateVersion = lib.trivial.release;

  virtualisation = {
    graphics = false;
    memorySize = 2048;
    cores = 2;
    # The web UI and SSH on the host's loopback; QEMU_NET_OPTS can add
    # or change forwardings when the VM is started.
    forwardPorts = [
      {
        from = "host";
        host.port = settings.timekprwPort;
        guest.port = settings.timekprwPort;
      }
      {
        from = "host";
        host.port = 2222;
        guest.port = 22;
      }
    ];
  };

  # A root shell on the console, for timekpra and the daemon's log.
  services.getty.autologinUser = "root";
  services.getty.helpLine = ''
    Web UI: http://localhost:${toString settings.timekprwPort}/ on the host, token "${settings.timekprwToken}".
    SSH:    ssh -p 2222 ${settings.alice}@localhost, password "${settings.alicePassword}".
  '';
}
