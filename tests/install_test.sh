#!/usr/bin/env bash
# install.sh: links the clone into a prefix, never overwrites a real file,
# uninstall removes only its own link. No network, no sudo.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
P=$(mktemp -d); trap 'rm -rf "$P"' EXIT
ok() { echo "ok   $*"; }; fail() { echo "FAIL $*"; exit 1; }

./install.sh --prefix "$P" --no-doctor >/dev/null
[ -L "$P/bin/glaring" ] && ok "install links glaring into the prefix" || fail "no link"
"$P/bin/glaring" --help | grep -q doctor && ok "the linked CLI runs from the prefix" || fail "linked CLI broken"
./install.sh --prefix "$P" --no-doctor >/dev/null && ok "install is idempotent" || fail "second install failed"

mkdir -p "$P/other/bin"; echo "mine" > "$P/other/bin/glaring"
if ./install.sh --prefix "$P/other" --no-doctor >/dev/null 2>&1; then fail "overwrote a real file"; fi
grep -q mine "$P/other/bin/glaring" && ok "refuses to overwrite a real file at the target" || fail "file clobbered"

ln -s /bin/ls "$P/other/bin/evil" ; ln -sfn /usr/bin/true "$P/other/bin/glaring.tmp"; rm "$P/other/bin/glaring"
ln -s /bin/ls "$P/other/bin/glaring"
if ./install.sh uninstall --prefix "$P/other" >/dev/null 2>&1; then fail "removed a foreign symlink"; fi
[ -L "$P/other/bin/glaring" ] && ok "uninstall refuses a link that does not point at a glaring clone" || fail "foreign link removed"

./install.sh uninstall --prefix "$P" >/dev/null
[ ! -e "$P/bin/glaring" ] && ok "uninstall removes the link" || fail "link remains"
[ -x ./glaring ] && ok "the clone itself is untouched" || fail "clone damaged"
if ./install.sh --prefix "$P" --bogus >/dev/null 2>&1; then fail "accepted a bogus option"; else ok "rejects unknown options"; fi
echo "INSTALL PASS"
