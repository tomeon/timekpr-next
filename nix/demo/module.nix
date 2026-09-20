# A machine running timekpr and its web front end, with users to try
# them on: the flake's `nixosModules.demo`.
#
# alice is a local user, bob@idm.nixos.test comes from a Kanidm identity
# server on the same machine (through its UNIX daemon and PAM); carol,
# dave and erin exist for trying out who may administer what.  SSH
# logins are tracked, so limits can be watched from an SSH session.  The
# NixOS test (../tests) runs this very module; `nixosConfigurations.demo`
# adds the QEMU VM profile (vm.nix) for running it locally.
#
# The settings the test needs to know are in settings.nix.
{
  config,
  lib,
  pkgs,
  ...
}: let
  settings = import ./settings.nix;
  inherit (settings) idmDomain alice alicePassword bob bobPassword idmAdminPassword carol dave erin kids timekprwToken timekprwPort;
  idmOrigin = "https://${idmDomain}";
  timekpr = config.services.timekpr.package;
  bash = "/run/current-system/sw/bin/bash";

  # Self-signed CA and server certificate for the Kanidm server.
  certs = pkgs.runCommand "idm-certs" {nativeBuildInputs = [pkgs.openssl];} ''
    mkdir -p "$out"
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
      -subj "/CN=Test CA" -keyout "$out/ca.key" -out "$out/ca.crt"
    openssl req -newkey rsa:2048 -nodes \
      -subj "/CN=${idmDomain}" -keyout "$out/server.key" -out server.csr
    printf '%s\n' \
      "subjectAltName=DNS:${idmDomain}" \
      "extendedKeyUsage=serverAuth" \
      "basicConstraints=CA:FALSE" > server.ext
    openssl x509 -req -days 3650 -in server.csr \
      -CA "$out/ca.crt" -CAkey "$out/ca.key" -CAcreateserial \
      -extfile server.ext -out "$out/server.crt"
  '';

  # timekpr only enforces limits on session types it is configured to
  # control, and by default it ignores "tty" sessions.  Logins over SSH
  # are "tty" sessions to logind, so track those too.
  timekprEtc = pkgs.runCommand "timekpr-etc" {} ''
    cp -r ${timekpr}/etc/timekpr "$out"
    chmod -R u+w "$out"
    substituteInPlace "$out/timekpr.conf" \
      --replace-fail 'TIMEKPR_SESSION_TYPES_CTRL = x11;wayland;mir' \
                     'TIMEKPR_SESSION_TYPES_CTRL = tty;x11;wayland;mir' \
      --replace-fail 'TIMEKPR_SESSION_TYPES_EXCL = tty;unspecified' \
                     'TIMEKPR_SESSION_TYPES_EXCL = unspecified'
  '';

  # The kanidm CLI insists on reading passwords from a terminal.  This
  # runs a command under a pseudo-terminal and answers every password
  # prompt with the given password.
  answerPassword =
    pkgs.writers.writePython3Bin "answer-password" {
      libraries = [pkgs.python3Packages.pexpect];
    } ''
      import sys

      import pexpect

      password, argv = sys.argv[1], sys.argv[2:]
      child = pexpect.spawn(argv[0], argv[1:], encoding="utf-8", timeout=300)
      child.logfile_read = sys.stdout
      while child.expect([r"(?i)password[^\r\n]*:\s*", pexpect.EOF]) == 0:
          child.sendline(password)
      child.close()
      sys.exit(1 if child.signalstatus is not None else child.exitstatus)
    '';
in {
  services.timekpr = {
    enable = true;
    # Members of the timekpr group are authorized by the rule the
    # package ships.
    adminUsers = [dave];
  };
  environment.etc.timekpr.source = lib.mkForce timekprEtc;

  # A site rule scoping erin to a single action for a single user.
  security.polkit.extraConfig = ''
    polkit.addRule(function(action, subject) {
      if (action.id == "com.timekpr.server.user.admin.time-left" &&
          subject.user == "${erin}" &&
          action.lookup("user") == "${alice}") {
        return polkit.Result.YES;
      }
    });
  '';

  # The web front end, socket activated.  The package's timekprw.socket
  # provides the UNIX socket; a TCP listener on every address is added
  # so that a VM's forwarded port reaches it.  The bearer token (needed
  # on TCP only) is handed over as a systemd credential.
  systemd.sockets.timekprw = {
    wantedBy = ["sockets.target"];
    listenStreams = ["0.0.0.0:${toString timekprwPort}"];
  };
  networking.firewall.allowedTCPPorts = [timekprwPort];
  systemd.services.timekprw.serviceConfig.LoadCredential = [
    "token:${pkgs.writeText "timekprw-token" timekprwToken}"
  ];

  # The user timekprw.service runs as (sysusers.d is not applied on
  # NixOS); polkit authorizes it through the timekpr group.
  users.groups.timekprw = {};
  # A group to hang a timekpr policy on (`timekpra --settimelimits @kids`).
  users.groups.${kids} = {};
  users.users = {
    timekprw = {
      isSystemUser = true;
      group = "timekprw";
      extraGroups = ["timekpr"];
    };
    ${alice} = {
      isNormalUser = true;
      password = alicePassword;
      extraGroups = [kids];
    };
    ${carol}.isNormalUser = true;
    ${dave}.isNormalUser = true;
    ${erin}.isNormalUser = true;
  };

  services.openssh = {
    enable = true;
    settings.PasswordAuthentication = true;
  };

  services.kanidm = {
    # Provisioning the idm_admin password needs the patched build.
    package = pkgs.kanidm_1_11.withSecretProvisioning;
    server = {
      enable = true;
      settings = {
        origin = idmOrigin;
        domain = idmDomain;
        bindaddress = "127.0.0.1:443";
        tls_chain = "${certs}/server.crt";
        tls_key = "${certs}/server.key";
      };
    };
    client = {
      enable = true;
      settings = {
        uri = idmOrigin;
        verify_ca = true;
        verify_hostnames = true;
      };
    };
    unix = {
      enable = true;
      settings.kanidm.pam_allowed_login_groups = ["posix_users"];
    };
    provision = {
      enable = true;
      idmAdminPasswordFile = pkgs.writeText "idm-admin-password" idmAdminPassword;
      groups.posix_users = {};
      # bob's membership of the kids group comes from Kanidm, so that a
      # policy on a domain group reaches a domain user through NSS.
      groups.${kids} = {};
      persons.${bob} = {
        displayName = "Bob";
        groups = ["posix_users" kids];
      };
    };
  };
  security.pki.certificateFiles = ["${certs}/ca.crt"];
  networking.hosts."127.0.0.1" = [idmDomain];

  # `services.kanidm.provision` cannot set POSIX attributes or passwords,
  # so bob gets those from the CLI once the server and its UNIX daemon
  # are up.  Every step is idempotent.
  systemd.services.demo-kanidm-users = {
    description = "Give bob a POSIX account and password in Kanidm";
    wantedBy = ["multi-user.target"];
    requires = ["kanidm.service" "kanidm-unixd.service"];
    after = ["kanidm.service" "kanidm-unixd.service"];
    path = [config.services.kanidm.package answerPassword];
    environment.HOME = "/var/lib/demo-kanidm-users";
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      StateDirectory = "demo-kanidm-users";
    };
    script = ''
      for _ in $(seq 60); do
        if kanidm-unix status | grep -q online; then
          break
        fi
        sleep 2
      done
      answer-password ${lib.escapeShellArg idmAdminPassword} kanidm login -D idm_admin
      kanidm group posix set --gidnumber 10000 posix_users
      kanidm group posix set --gidnumber 10002 ${kids}
      kanidm person posix set --gidnumber 10001 --shell ${bash} ${bob}
      answer-password ${lib.escapeShellArg bobPassword} kanidm person posix set-password ${bob}
    '';
  };

  environment.systemPackages = [
    answerPassword
    pkgs.curl
    pkgs.sshpass
  ];
}
