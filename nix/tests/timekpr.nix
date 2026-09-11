# NixOS integration test for timekpr.
#
# A single machine runs the upstream `services.timekpr` module with this
# flake's timekpr package, a local user (alice), and a Kanidm identity
# server whose UNIX daemon provides a POSIX user (bob@idm.nixos.test).
# For each user the test (timekpr.py) logs in over SSH (through PAM) and
# checks that timekpr terminates the session when the user has no screen
# time left, and leaves it alone once the user has been granted extra time.
# The settings are made through timekpra talking D-Bus for one user and
# through timekpra talking to the web front end (timekprw, socket
# activated, over its UNIX socket) for the other; the web API is also
# checked directly against what timekpra reports.
#
# `backend` selects how the machine is run: "vm" for a QEMU virtual
# machine (`nodes`), "container" for a systemd-nspawn container
# (`containers`), which starts faster and does not need KVM.
{
  timekpr,
  backend ? "vm",
}: {
  pkgs,
  lib,
  ...
}: let
  idmDomain = "idm.nixos.test";
  idmOrigin = "https://${idmDomain}";

  alice = "alice";
  alicePassword = "alice-password";
  bob = "bob";
  # Kanidm enforces a minimum length and quality for UNIX passwords.
  bobPassword = "Nk7rP2xW9qL4mZ8vT3bH";
  idmAdminPassword = "idm-admin-password";
  # Users for the authorization checks: carol has no privileges, dave
  # is in the timekpr group, erin is scoped by the polkit rule below.
  carol = "carol";
  dave = "dave";
  erin = "erin";
  timekprwToken = "timekprw-test-token";
  timekprwTokenFile = pkgs.writeText "timekprw-token" timekprwToken;
  timekprwPort = 8463;
  # the UNIX socket of the package's timekprw.socket unit
  timekprwSocket = "/run/timekprw/timekprw.sock";

  # Values the test script needs; see the top of timekpr.py.
  testConfig = {
    inherit alice alicePassword bobPassword idmAdminPassword carol dave erin timekprwToken timekprwPort timekprwSocket;
    timekprwTokenFile = "${timekprwTokenFile}";
    bob = "${bob}@${idmDomain}";
    timekprPackage = "${timekpr}";
  };

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
  # control, and by default it ignores "tty" sessions.  The test logs in
  # over SSH, which logind registers as a "tty" session, so track those.
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

  machine = {
    config,
    pkgs,
    ...
  }: {
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

    services.timekpr = {
      enable = true;
      package = timekpr;
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
    # provides the UNIX socket; a TCP listener is added to it here.  The
    # bearer token (needed on TCP only) is handed over as a systemd
    # credential.
    systemd.sockets.timekprw = {
      wantedBy = ["sockets.target"];
      listenStreams = ["127.0.0.1:${toString timekprwPort}"];
    };
    systemd.services.timekprw.serviceConfig.LoadCredential = [
      "token:${timekprwTokenFile}"
    ];

    users.users = {
      ${alice} = {
        isNormalUser = true;
        password = alicePassword;
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
        persons.${bob} = {
          displayName = "Bob";
          groups = ["posix_users"];
        };
      };
    };
    security.pki.certificateFiles = ["${certs}/ca.crt"];
    networking.hosts."127.0.0.1" = [idmDomain];

    environment.systemPackages = [
      answerPassword
      pkgs.curl
      pkgs.sshpass
    ];
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
