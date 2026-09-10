{
  description = "timekpr-next: keep control of computer usage";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    flake-parts = {
      url = "github:hercules-ci/flake-parts";
      inputs.nixpkgs-lib.follows = "nixpkgs";
    };

    devshell = {
      url = "github:numtide/devshell";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    treefmt-nix = {
      url = "github:numtide/treefmt-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = inputs @ {flake-parts, ...}:
    flake-parts.lib.mkFlake {inherit inputs;} {
      imports = [
        inputs.devshell.flakeModule
        inputs.treefmt-nix.flakeModule
      ];

      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];

      perSystem = {
        config,
        pkgs,
        ...
      }: {
        packages = {
          timekpr = pkgs.timekpr.overrideAttrs (_: {
            src = pkgs.lib.fileset.toSource {
              root = ./.;
              fileset = pkgs.lib.fileset.difference ./. (pkgs.lib.fileset.unions [
                ./flake.nix
                ./flake.lock
                ./nix
              ]);
            };
          });

          default = config.packages.timekpr;
        };

        checks = pkgs.lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
          timekpr = pkgs.testers.nixosTest (import ./nix/tests/timekpr.nix {
            inherit (config.packages) timekpr;
          });
        };

        # The same test on a systemd-nspawn container instead of a QEMU VM.
        # Not a flake check because it needs the Nix daemon configured with
        # auto-allocate-uids, the uid-range system feature, and the
        # auto-allocate-uids and cgroups experimental features, and a
        # cgroup v2 host.  Build it with `nix build .#tests.timekpr-container`.
        legacyPackages = pkgs.lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
          tests.timekpr-container = pkgs.testers.nixosTest (import ./nix/tests/timekpr.nix {
            inherit (config.packages) timekpr;
            backend = "container";
          });
        };

        treefmt = {
          projectRootFile = "flake.nix";
          programs.alejandra.enable = true;
        };

        devshells.default = {
          packages = [config.treefmt.build.wrapper];
        };
      };
    };
}
