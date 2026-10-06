#!/bin/sh
# Clone-and-install helper for glaring. Run it from your clone.
#
#   ./install.sh [install] [--prefix DIR] [--build] [--no-doctor]
#   ./install.sh uninstall [--prefix DIR] [--images] [--purge]
#
# It downloads nothing and never uses sudo. `install` links
# <prefix>/bin/glaring to this clone (so `git pull` upgrades it), checks
# your PATH, runs `glaring doctor`, and with --build builds the images.
# `uninstall` removes only that link; --images also removes the glaring
# images, and --purge also stops pods and deletes their volumes and state.
set -eu

here=$(cd "$(dirname "$0")" && pwd -P)
prefix="${HOME}/.local"
action=install
build=0
doctor=1
images=0
purge=0

die() { echo "install.sh: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    install|uninstall) action=$1 ;;
    --prefix) [ $# -ge 2 ] || die "--prefix needs a directory"; prefix=$2; shift ;;
    --build) build=1 ;;
    --no-doctor) doctor=0 ;;
    --images) images=1 ;;
    --purge) purge=1 ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
  shift
done

[ -x "$here/glaring" ] || die "run this from a glaring clone (no ./glaring next to install.sh)"
command -v python3 >/dev/null 2>&1 || die "python3 not found (need 3.8+); install it, then re-run"
[ "$(id -u)" -ne 0 ] || die "do not run as root; glaring installs into your own prefix"

link="$prefix/bin/glaring"

case "$action" in
  install)
    mkdir -p "$prefix/bin"
    if [ -e "$link" ] && [ ! -L "$link" ]; then
      die "$link exists and is not a symlink; refusing to overwrite it"
    fi
    if [ -L "$link" ]; then
      case "$(readlink "$link")" in
        */glaring) ;;
        *) die "$link is a symlink to something other than a glaring clone; refusing to replace it" ;;
      esac
    fi
    ln -sfn "$here/glaring" "$link"
    echo "linked $link -> $here/glaring"
    case ":$PATH:" in
      *":$prefix/bin:"*) ;;
      *) echo "note: $prefix/bin is not on your PATH. Add this to your shell profile:"
         echo "      export PATH=\"$prefix/bin:\$PATH\"" ;;
    esac
    if [ "$build" -eq 1 ]; then
      "$link" build
    fi
    if [ "$doctor" -eq 1 ]; then
      echo
      "$link" doctor --offline || echo "(doctor reported problems; fix them before 'glaring up')"
    fi
    ;;
  uninstall)
    if [ -L "$link" ]; then
      target=$(readlink "$link")
      case "$target" in
        */glaring) rm -f "$link"; echo "removed $link" ;;
        *) die "$link points somewhere unexpected ($target); not removing" ;;
      esac
    else
      echo "no glaring link at $link"
    fi
    if [ "$purge" -eq 1 ]; then
      state="${GLARING_STATE:-$HOME/.local/state/glaring}"
      case "$state" in
        /*) ;;
        *) die "GLARING_STATE must be an absolute path (got: $state)" ;;
      esac
      case "$state" in
        */../*|*/..|*/./*|*/.) die "refusing to purge state path containing . or .. components: $state" ;;
      esac
      [ "$(basename "$state")" = glaring ] || die "refusing to purge $state: its last component must be 'glaring'"
      [ "$state" != "$HOME" ] && [ "$state" != "/" ] || die "refusing to purge $state"
      if [ -d "$state" ] && [ ! -f "$state/tokens.json" ] && [ ! -d "$state/pods" ]; then
        die "refusing to purge $state: it does not look like glaring state (no tokens.json or pods/)"
      fi
      "$here/glaring" down --purge || true
      rm -rf -- "$state"
      echo "purged pods, volumes and glaring state"
    fi
    if [ "$images" -eq 1 ] || [ "$purge" -eq 1 ]; then
      docker rmi glaring-pod:latest glaring-proxy:latest >/dev/null 2>&1 || true
      echo "removed glaring images (if present)"
    fi
    ;;
esac
