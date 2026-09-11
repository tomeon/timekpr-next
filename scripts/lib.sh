# Shared shell helpers for the scripts in this directory.  Source it:
#   . "$(dirname "$0")/lib.sh"

log() {
  printf '%s: %s\n' "${0##*/}" "$*" >&2
}

die() {
  log "$@"
  exit 1
}

# Some environments install Nix without putting it on PATH.
ensure_nix_on_path() {
  if ! command -v nix > /dev/null; then
    PATH="$HOME/.nix-profile/bin:/nix/var/nix/profiles/default/bin:$PATH"
    export PATH
  fi
  command -v nix > /dev/null || die "nix not found on PATH"
}

repo_root() {
  git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel
}

# Per-user scratch space for these scripts, outside the working tree so
# that nothing here ends up in the package source.
cache_dir() {
  local dir="${XDG_CACHE_HOME:-$HOME/.cache}/timekpr-next/$1"
  mkdir -p "$dir"
  printf '%s\n' "$dir"
}

current_system() {
  nix eval --impure --raw --expr builtins.currentSystem
}
