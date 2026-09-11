# Helper scripts

Tools for developing and testing this flake in restricted environments:
sandboxes that reach the internet only through a loopback proxy with a
private CA, that block GitHub's API and tarball downloads while allowing
anonymous `git` access, that have no `/dev/kvm`, or that install Nix
without putting it on `PATH`.  On an unrestricted machine they behave
like the plain commands they wrap.

All of them are also available as commands in the devshell (`menu`).

| Script | Purpose |
| --- | --- |
| `flake-inputs-via-git` | Put the locked `github:` flake inputs into the Nix store by fetching them over git, or `--update` them to their newest commit and rewrite `flake.lock`. Replaces `nix flake update` where the GitHub API is unreachable. |
| `flake-check` | `nix flake check -L`, advertising the `kvm` system feature when `/dev/kvm` is missing so the NixOS test runs under emulation instead of being refused. |
| `run-nixos-test` | Build a check's test driver and run it outside the Nix sandbox, streaming the console log; `-i` for the interactive driver. |
| `act-sandboxed` | Run the GitHub Actions workflows with `act`, wiring the job container to a loopback proxy and private CA, feeding it the flake inputs through a signed `file://` binary cache, and starting `dockerd` if needed. |
| `github-latest-tags` | Newest version tags of a GitHub repository via `git ls-remote`. |

`lib.sh` holds the shared shell helpers.  Scratch data (git mirrors, the
binary cache, test run logs) lives under `$XDG_CACHE_HOME/timekpr-next`,
never in the working tree.
