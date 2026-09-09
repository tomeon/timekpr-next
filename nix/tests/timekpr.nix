# NixOS integration test for timekpr.
#
# A single machine runs the upstream `services.timekpr` module with this
# flake's timekpr package, a local user (alice), and a Kanidm identity
# server whose UNIX daemon provides a POSIX user (bob@idm.nixos.test).
# For each user the test logs in over SSH (through PAM) and checks that
# timekpr terminates the session when the user has no screen time left,
# and leaves it alone once the user has been granted extra time.
{timekpr}: {
  pkgs,
  lib,
  ...
}: let
  idmDomain = "idm.nixos.test";
  idmOrigin = "https://${idmDomain}";

  alicePassword = "alice-password";
  # Kanidm enforces a minimum length and quality for UNIX passwords.
  bobPassword = "Nk7rP2xW9qL4mZ8vT3bH";
  idmAdminPassword = "idm-admin-password";

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
in {
  name = "timekpr";

  nodes.machine = {
    config,
    pkgs,
    ...
  }: {
    virtualisation.memorySize = 2048;
    virtualisation.cores = 2;

    services.timekpr = {
      enable = true;
      package = timekpr;
    };
    environment.etc.timekpr.source = lib.mkForce timekprEtc;

    users.users.alice = {
      isNormalUser = true;
      password = alicePassword;
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
          bindaddress = "[::]:443";
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
        persons.bob = {
          displayName = "Bob";
          groups = ["posix_users"];
        };
      };
    };
    security.pki.certificateFiles = ["${certs}/ca.crt"];
    networking.hosts = {
      "127.0.0.1" = [idmDomain];
      "::1" = [idmDomain];
    };

    environment.systemPackages = [
      answerPassword
      pkgs.sshpass
    ];
  };

  testScript = ''
    import shlex

    ALICE = "alice"
    BOB = "bob@${idmDomain}"
    ALICE_PASSWORD = ${builtins.toJSON alicePassword}
    BOB_PASSWORD = ${builtins.toJSON bobPassword}
    IDM_ADMIN_PASSWORD = ${builtins.toJSON idmAdminPassword}
    BASH = "/run/current-system/sw/bin/bash"

    # How long each SSH login keeps its session open.  timekpr polls every
    # 3 seconds and terminates an over-limit session after a 15 second
    # countdown, so this leaves plenty of margin either way.
    HOLD = 45
    # Per-weekday limits (Mon..Sun) in seconds: no screen time at all.
    NO_TIME = "0;0;0;0;0;0;0"
    # Extra time granted for today, in seconds.
    EXTRA_TIME = "300"
    TIMEKPR_LOG = "/var/log/timekpr.log"


    def timekpra(*args):
        return machine.succeed("timekpra " + " ".join(map(shlex.quote, args)))


    def ssh_login(user, password):
        """Log in over SSH with a password, keep the session open for HOLD
        seconds, and return the command's (status, output)."""
        remote = f"sleep {HOLD}; echo SURVIVED"
        cmd = " ".join([
            "sshpass", "-p", shlex.quote(password),
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-l", shlex.quote(user), "localhost",
            shlex.quote(remote),
        ])
        return machine.execute(cmd, timeout=HOLD + 300)


    def wait_until_logged_out(user):
        machine.wait_until_fails(
            f"loginctl list-users --no-legend | grep -F {shlex.quote(user)}"
        )
        machine.wait_until_succeeds(
            f"grep -F 'user \"{user}\" has gone' {TIMEKPR_LOG}"
        )


    def expect_login_survives(user, password):
        status, out = ssh_login(user, password)
        assert status == 0 and "SURVIVED" in out, (
            f"login as {user} was cut short: status={status}, output={out!r}"
        )
        wait_until_logged_out(user)


    def expect_login_terminated(user, password):
        status, out = ssh_login(user, password)
        assert status != 0 and "SURVIVED" not in out, (
            f"login as {user} was not terminated: status={status}, output={out!r}"
        )
        # timekpr flushes its log file lazily, so wait for the line.
        machine.wait_until_succeeds(
            f"grep -F 'killing \"{user}\" session' {TIMEKPR_LOG}"
        )
        wait_until_logged_out(user)


    def exercise(user, password):
        with subtest(f"{user}: an unrestricted login survives"):
            expect_login_survives(user, password)

        with subtest(f"{user}: timekpr knows the user"):
            assert user in timekpra("--userlist")

        with subtest(f"{user}: forbid all screen time"):
            timekpra("--settimelimits", user, NO_TIME)

        with subtest(f"{user}: a restricted login is terminated"):
            expect_login_terminated(user, password)

        with subtest(f"{user}: grant {EXTRA_TIME} seconds of extra time"):
            timekpra("--settimeleft", user, "+", EXTRA_TIME)

        with subtest(f"{user}: an exempted login survives"):
            expect_login_survives(user, password)


    def main():
        machine.wait_for_unit("multi-user.target")
        machine.wait_for_unit("timekpr.service")
        machine.wait_for_open_port(22)

        with subtest("timekpr runs this flake's package"):
            assert "${timekpr}" in machine.succeed(
                "systemctl show -p ExecStart --value timekpr.service"
            )

        exercise(ALICE, ALICE_PASSWORD)

        with subtest("kanidm: server, provisioning, and UNIX daemon are up"):
            machine.wait_for_unit("kanidm.service")
            machine.wait_for_unit("kanidm-unixd.service")
            machine.wait_for_file("/run/kanidm-unixd/sock")
            machine.wait_until_succeeds("kanidm-unix status | grep -q online")

        with subtest("kanidm: make bob a POSIX user with a UNIX password"):
            machine.succeed(
                f"answer-password {shlex.quote(IDM_ADMIN_PASSWORD)}"
                " kanidm login -D idm_admin"
            )
            machine.succeed("kanidm group posix set --gidnumber 10000 posix_users")
            machine.succeed(
                f"kanidm person posix set --gidnumber 10001 --shell {BASH} bob"
            )
            machine.succeed(
                f"answer-password {shlex.quote(BOB_PASSWORD)}"
                " kanidm person posix set-password bob"
            )
            machine.wait_until_succeeds(f"getent passwd {shlex.quote(BOB)}")

        exercise(BOB, BOB_PASSWORD)

    machine.start()
    try:
        main()
    except Exception:
        # timekpr buffers its log and flushes it on shutdown; make it
        # available for diagnosing the failure.
        machine.execute("systemctl stop timekpr.service")
        print(machine.execute(f"cat {TIMEKPR_LOG}")[1])
        raise
  '';
}
