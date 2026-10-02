#!/bin/sh
set -eu
fail() { printf 'INSTALL_FAILED: %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || fail 'Run as root' 
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ "$(id -u ac-ci-builder)" -eq 1001 ] || fail 'Expected ac-ci-builder UID 1001'
[ -x /usr/local/bin/podman ] || fail 'Missing executable /usr/local/bin/podman'
command -v python3 >/dev/null || fail 'python3 is required'
command -v visudo >/dev/null || fail 'visudo is required'
visudo -c || fail 'Fix the existing sudoers errors shown above, then rerun' 
# Validate Python without writing __pycache__ into the checkout.
python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$base/ac-nginx-import"
for path in /var/spool/ac-nginx /var/spool/ac-nginx/inbox /var/lib/ac-nginx /var/lib/ac-nginx/imports; do
    [ ! -L "$path" ] || { echo "Refusing symlink: $path" >&2; exit 1; }
done
if [ ! -e /var/lib/ac-nginx ]; then
    install -d -o root -g root -m 0755 /var/lib/ac-nginx
fi
python3 - <<'CHECK' || fail 'Unsafe /var/lib/ac-nginx permissions; see above'
import os, stat, sys
p = '/var/lib/ac-nginx'
s = os.stat(p)
if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
    print(f'{p}: expected root-owned directory without group/other write; '
          f'got UID={s.st_uid} mode={stat.S_IMODE(s.st_mode):04o}', file=sys.stderr)
    sys.exit(1)
CHECK
install -d -o root -g root -m 0755 /var/spool/ac-nginx
install -d -o ac-ci-builder -g "$(id -gn ac-ci-builder)" -m 0700 /var/spool/ac-nginx/inbox
install -d -o root -g root -m 0700 /var/lib/ac-nginx/imports
rule=$(mktemp)
trap 'rm -f "$rule"' EXIT
printf '%s\n' 'ac-ci-builder ALL=(root) NOPASSWD: /usr/local/sbin/ac-nginx-import *' > "$rule"
visudo -cf "$rule"
install -o root -g root -m 0755 "$base/ac-nginx-import" /usr/local/sbin/ac-nginx-import
install -o root -g root -m 0440 "$rule" /etc/sudoers.d/ac-nginx-import
visudo -c
echo 'INSTALLED: local image import only; nginx1 unchanged'
