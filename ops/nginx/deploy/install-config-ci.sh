#!/bin/sh
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run as root' >&2; exit 1; }
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ "$(id -u ac-ci-builder)" = 1001 ]
# Install trusted code using the normal deployment installer and lock.
sh "$base/install.sh"
exec 9>/var/lib/ac-nginx/deployments/lock
flock -n 9
python3 - <<'PY'
import os, pathlib, pwd, stat
account = pwd.getpwnam('ac-ci-builder')
parent = pathlib.Path('/var/spool/ac-nginx')
for p in list(reversed(parent.parents)) + [parent]:
    st = p.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
        raise SystemExit('Unsafe parent: ' + str(p))
inbox = parent / 'config-inbox'
if inbox.exists() or inbox.is_symlink():
    st = inbox.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != account.pw_uid or stat.S_IMODE(st.st_mode) != 0o700:
        raise SystemExit('Unexpected config-inbox ownership/mode')
else:
    inbox.mkdir(mode=0o700)
    os.chown(inbox, account.pw_uid, account.pw_gid)
PY
rule=$(mktemp)
trap 'rm -f "$rule"' EXIT
printf '%s\n' 'ac-ci-builder ALL=(root) NOPASSWD: /usr/local/sbin/ac-nginx-config-prepare --import *' > "$rule"
visudo -cf "$rule"
install -o root -g root -m 0440 "$rule" /etc/sudoers.d/ac-nginx-config-import
visudo -c
echo 'CONFIG_CI_INSTALLED: no deployment performed'
