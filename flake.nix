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

      flake = {
        # The demo machine: timekpr, its web front end, and users to try
        # them on (nix/demo/module.nix), with this flake's timekpr package.
        nixosModules.demo = {pkgs, ...}: {
          imports = [./nix/demo/module.nix];
          services.timekpr.package = inputs.self.packages.${pkgs.stdenv.hostPlatform.system}.timekpr;
        };

        # The demo machine as a QEMU VM; see docs/demo-vm.md.
        nixosConfigurations = let
          demo = system:
            inputs.nixpkgs.lib.nixosSystem {
              inherit system;
              modules = [
                inputs.self.nixosModules.demo
                ./nix/demo/vm.nix
              ];
            };
        in {
          demo = demo "x86_64-linux";
          demo-aarch64-linux = demo "aarch64-linux";
        };
      };

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
                ./docs/proposals
                ./flake.nix
                ./flake.lock
                ./nix
                ./ruff.toml
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

        checks = let
          # Tests of the web front end against a fake daemon connector (no
          # D-Bus, no VM): the pytest suite in nix/tests/web.
          webTests = name: extraPackages: env: files:
            pkgs.runCommand name ({
                nativeBuildInputs = [
                  (pkgs.python3.withPackages (ps:
                    with ps;
                      [
                        dbus-python
                        fastapi
                        httpx
                        psutil
                        pygobject3
                        pytest
                        uvicorn
                      ]
                      ++ extraPackages ps))
                ];
              }
              // env) ''
              mkdir pkg
              ln -s ${config.packages.timekpr.src} pkg/timekpr
              export PYTHONPATH="$PWD/pkg" HOME="$TMPDIR"
              pytest -p no:cacheprovider --basetemp="$TMPDIR/pytest" ${files}
              touch "$out"
            '';
        in
          {
            # The API, the conversions, the listeners, socket activation and
            # timekpra's HTTP connector.
            web = webTests "timekpr-web-tests" (_: []) {} ./nix/tests/web;
          }
          // pkgs.lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
            # The web UI in a headless Chromium driven by Playwright.
            web-ui = webTests "timekpr-web-ui-tests" (ps: [ps.playwright]) {
              PLAYWRIGHT_BROWSERS_PATH = pkgs.playwright-driver.browsers-chromium;
              PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS = "true";
            } "${./nix/tests/web}/test_ui.py";

            timekpr = pkgs.testers.nixosTest (import ./nix/tests/timekpr.nix {
              inherit (config.packages) timekpr;
              demoModule = inputs.self.nixosModules.demo;
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
            demoModule = inputs.self.nixosModules.demo;
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
          # The flake's own Python, which has no file extension either.
          # ruff runs over timekpr's sources as well, on the default
          # `*.py` includes; `ruff.toml` says which of its rules do not
          # apply to them.
          pythonScripts = [
            "nix/tests/timekpr.py"
            "nix/tests/web/*.py"
            "scripts/flake-inputs-via-git"
          ];
        in
          treefmt: {
            projectRootFile = "flake.nix";
            programs.alejandra.enable = true;
            programs.shellcheck = {
              enable = true;
              includes = treefmt.options.programs.shellcheck.includes.default ++ shellScripts;
              # Resolve `source` directives relative to the sourcing script.
              source-path = "SCRIPTDIR";
            };
            programs.shfmt = {
              enable = true;
              includes = treefmt.options.programs.shfmt.includes.default ++ shellScripts;
            };
            programs.prettier.enable = true;
            programs.ruff-check = {
              enable = true;
              includes = treefmt.options.programs.ruff-check.includes.default ++ pythonScripts;
            };
            programs.ruff-format = {
              enable = true;
              includes = treefmt.options.programs.ruff-format.includes.default ++ pythonScripts;
            };
            programs.xmllint.enable = true;
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
