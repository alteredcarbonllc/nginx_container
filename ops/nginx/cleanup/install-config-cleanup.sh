#!/bin/sh
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run as root' >&2; exit 1; }
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ -f /usr/local/libexec/ac_nginx_release.py ]
exec 9>/var/lib/ac-nginx/deployments/lock
flock -n 9
python3 - "$base" <<'PY'
import ast, pathlib, sys
for name in ('ac-nginx-cleanup.py', 'ac-nginx-config-cleanup.py'):
    ast.parse((pathlib.Path(sys.argv[1]) / name).read_text())
PY
backup=$(mktemp -d /var/lib/ac-nginx/cleanup-install.XXXXXX)
cp -a /usr/local/sbin/ac-nginx-cleanup "$backup/"
if [ -e /usr/local/sbin/ac-nginx-config-cleanup ]; then
    cp -a /usr/local/sbin/ac-nginx-config-cleanup "$backup/"
fi
install -o root -g root -m 0755 "$base/ac-nginx-config-cleanup.py" /usr/local/sbin/ac-nginx-config-cleanup
install -o root -g root -m 0755 "$base/ac-nginx-cleanup.py" /usr/local/sbin/ac-nginx-cleanup
printf 'CONFIG_CLEANUP_INSTALLED: backup=%s; activation flag unchanged\n' "$backup"
