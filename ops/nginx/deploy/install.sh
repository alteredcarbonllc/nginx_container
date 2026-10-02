#!/bin/sh
set -eu
fail() { printf 'INSTALL_FAILED: %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || fail 'Run as root'
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for tool in /usr/local/bin/podman /usr/local/sbin/ac-nginxctl /usr/bin/python3 /usr/bin/curl /usr/bin/sv; do
    [ -x "$tool" ] || fail "Missing $tool"
done
[ -d /etc/ac/runit/nginx ] || fail 'NGINX runit service is missing'
[ -d /var/lib/ac-nginx/imports ] || fail 'Install local import first'
visudo -c || fail 'Fix existing sudoers errors'
python3 - "$base" <<'PY'
import ast, pathlib, stat, sys
base = pathlib.Path(sys.argv[1])
for name in (base / 'ac-nginx-deploy', base.parent / 'verify-inspect.py'):
    ast.parse(name.read_text())
for name in ('/var/lib/ac-nginx', '/var/lib/ac-nginx/imports', '/var/lib/ac-nginx/deployments'):
    p = pathlib.Path(name)
    if p.is_symlink():
        sys.exit('Refusing symlink: ' + name)
    if p.exists():
        s = p.stat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            sys.exit('Expected root-owned directory without group/other write: ' + name)
PY
rule=$(mktemp)
trap 'rm -f "$rule"' EXIT
printf '%s\n' 'ac-ci-builder ALL=(root) NOPASSWD: /usr/local/sbin/ac-nginx-deploy *' > "$rule"
visudo -cf "$rule"
install -d -o root -g root -m 0700 /var/lib/ac-nginx/deployments
install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 "$base/../verify-inspect.py" /usr/local/libexec/ac-nginx-verify-inspect
install -o root -g root -m 0755 "$base/ac-nginx-deploy" /usr/local/sbin/ac-nginx-deploy
install -o root -g root -m 0440 "$rule" /etc/sudoers.d/ac-nginx-deploy
visudo -c
echo 'INSTALLED: ac-nginx-deploy; no deployment performed'
