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
          timekpr = pkgs.timekpr.overrideAttrs (old: {
            src = pkgs.lib.fileset.toSource {
              root = ./.;
              fileset = pkgs.lib.fileset.difference ./. (pkgs.lib.fileset.unions [
                ./.actrc
                ./.github
                ./flake.nix
                ./flake.lock
                ./nix
                ./scripts
              ]);
            };
            # nixpkgs rewrites /usr/bin/timekpr in every .policy file and
            # fails on one that does not mention it.  Only the pkexec policy
            # does; the polkit actions for the D-Bus interface do not.
            postPatch = builtins.replaceStrings ["**/*.policy"] ["**/*.pkexec.policy"] old.postPatch;
            # timekprw, the web front end, needs these on top of nixpkgs'
            # dependency list.
            propagatedBuildInputs =
              old.propagatedBuildInputs
              ++ (with pkgs.python3Packages; [
                fastapi
                uvicorn
              ]);
          });

          default = config.packages.timekpr;
        };

        checks =
          {
            # Unit tests for the web front end and timekpra's HTTP connector,
            # run against a fake daemon connector (no D-Bus, no VM).
            web =
              pkgs.runCommand "timekpr-web-tests" {
                nativeBuildInputs = [
                  (pkgs.python3.withPackages (ps:
                    with ps; [
                      dbus-python
                      fastapi
                      httpx
                      psutil
                      pygobject3
                      pytest
                      uvicorn
                    ]))
                ];
              } ''
                mkdir pkg
                ln -s ${config.packages.timekpr.src} pkg/timekpr
                export PYTHONPATH="$PWD/pkg"
                pytest -p no:cacheprovider --basetemp="$TMPDIR/pytest" ${./nix/tests/web}
                touch "$out"
              '';
          }
          // pkgs.lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
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

        treefmt = let
          # The helper scripts have no file extensions, so list them.
          shellScripts = [
            "scripts/act-sandboxed"
            "scripts/flake-check"
            "scripts/github-latest-tags"
            "scripts/lib.sh"
            "scripts/run-nixos-test"
          ];
          # Only Python written for this flake; timekpr's own sources are
          # left as upstream formats them.
          pythonScripts = [
            "nix/tests/timekpr.py"
            "nix/tests/web/*.py"
            "scripts/flake-inputs-via-git"
          ];
        in {
          projectRootFile = "flake.nix";
          programs.alejandra.enable = true;
          programs.shellcheck = {
            enable = true;
            includes = shellScripts;
            # Resolve `source` directives relative to the sourcing script.
            source-path = "SCRIPTDIR";
          };
          programs.shfmt = {
            enable = true;
            includes = shellScripts;
          };
          programs.ruff-check = {
            enable = true;
            includes = pythonScripts;
          };
          programs.ruff-format = {
            enable = true;
            includes = pythonScripts;
          };
        };

        devshells.default = let
          # Expose a script from ./scripts as a devshell command.
          script = name: help: {
            inherit name help;
            category = "sandbox helpers";
            command = ''exec "$PRJ_ROOT/scripts/${name}" "$@"'';
          };
        in {
          packages = [
            config.treefmt.build.wrapper
            pkgs.git
            pkgs.python3
          ];
          commands = [
            {package = pkgs.act;}
            (script "flake-inputs-via-git" "fetch or update github: flake inputs over git, without the GitHub API")
            (script "flake-check" "nix flake check, running NixOS tests under emulation if there is no KVM")
            (script "run-nixos-test" "build a NixOS test driver and run it outside the Nix sandbox")
            (script "act-sandboxed" "run the GitHub Actions workflows with act behind a loopback proxy")
            (script "github-latest-tags" "list a GitHub repository's newest version tags via git")
          ];
        };
      };
    };
}
