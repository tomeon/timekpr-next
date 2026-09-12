# Values shared by the demo machine (module.nix) and the NixOS test
# (../tests/timekpr.py gets them as CONFIG).  Everything here is public
# demo data, not secrets.
{
  idmDomain = "idm.nixos.test";

  alice = "alice";
  alicePassword = "alice-password";
  bob = "bob";
  # Kanidm enforces a minimum length and quality for UNIX passwords.
  bobPassword = "Nk7rP2xW9qL4mZ8vT3bH";
  idmAdminPassword = "idm-admin-password";
  # Users for the authorization checks: carol has no privileges, dave
  # is in the timekpr group, erin is scoped by a polkit rule.
  carol = "carol";
  dave = "dave";
  erin = "erin";

  # The web front end: the bearer token clients on TCP must present,
  # the TCP port, and the UNIX socket of the package's timekprw.socket.
  timekprwToken = "timekprw-test-token";
  timekprwPort = 8463;
  timekprwSocket = "/run/timekprw/timekprw.sock";
}
