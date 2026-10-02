#!/bin/sh
set -eu
[ "$(id -u)" -eq 0 ] || { echo 'Run as root' >&2; exit 1; }
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ "$(id -u ac-ci-builder)" -eq 1001 ]
[ -x /usr/local/bin/podman ]
command -v python3 >/dev/null
command -v visudo >/dev/null
# Validate Python without writing __pycache__ into the checkout.
python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$base/ac-nginx-import"
for path in /var/spool/ac-nginx /var/spool/ac-nginx/inbox /var/lib/ac-nginx /var/lib/ac-nginx/imports; do
    [ ! -L "$path" ] || { echo "Refusing symlink: $path" >&2; exit 1; }
done
install -d -o root -g root -m 0755 /var/spool/ac-nginx
install -d -o ac-ci-builder -g "$(id -gn ac-ci-builder)" -m 0700 /var/spool/ac-nginx/inbox
install -d -o root -g root -m 0700 /var/lib/ac-nginx/imports
[ "$(stat -c %u /var/lib/ac-nginx)" -eq 0 ]
[ "$(stat -c %a /var/lib/ac-nginx)" = 755 ] || [ "$(stat -c %a /var/lib/ac-nginx)" = 700 ]
rule=$(mktemp)
trap 'rm -f "$rule"' EXIT
printf '%s\n' 'ac-ci-builder ALL=(root) NOPASSWD: /usr/local/sbin/ac-nginx-import *' > "$rule"
visudo -cf "$rule"
install -o root -g root -m 0755 "$base/ac-nginx-import" /usr/local/sbin/ac-nginx-import
install -o root -g root -m 0440 "$rule" /etc/sudoers.d/ac-nginx-import
visudo -c
echo 'INSTALLED: local image import only; nginx1 unchanged'
