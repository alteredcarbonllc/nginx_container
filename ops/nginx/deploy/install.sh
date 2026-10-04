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
exec 9>/var/lib/ac-nginx/deployments/lock
flock -n 9 || fail 'Deployment/cleanup is running'
visudo -c || fail 'Fix existing sudoers errors'
python3 - "$base" <<'PY'
import ast, pathlib, stat, sys
base = pathlib.Path(sys.argv[1])
for name in (base / 'ac-nginx-deploy', base.parent / 'verify-inspect.py', base / 'ac_nginx_release.py', base / 'ac-nginx-config-tools', base / 'ac-nginx-config-prepare'):
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
# Pin the already downloaded HTTP/3 client. Never pull during installation/deploy.
python3 - <<'PYCODE'
import os, pathlib, re, stat, subprocess
pin = pathlib.Path('/etc/ac/nginx-http3-image')
for parent in reversed(pin.parents):
    st = parent.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
        raise SystemExit('Untrusted pin directory: ' + str(parent))
pod = ['/usr/local/bin/podman', '--remote=false']
if pin.exists() or pin.is_symlink():
    st = pin.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
        raise SystemExit('Untrusted HTTP/3 pin')
    image = pin.read_text().strip()
else:
    image = subprocess.check_output(pod + ['image', 'inspect', '--format', '{{.Id}}',
        'ghcr.io/macbre/curl-http3:latest'], text=True, timeout=30).strip()
    image = 'sha256:' + image.removeprefix('sha256:')
if not re.fullmatch('sha256:[0-9a-f]{64}', image):
    raise SystemExit('Invalid HTTP/3 image ID')
version = subprocess.check_output(pod + ['run', '--rm', '--pull=never', '--network', 'none',
    image, 'curl', '-V'], text=True, timeout=30)
if not any(line.startswith('Features:') and 'HTTP3' in line.split() for line in version.splitlines()):
    raise SystemExit('Pinned curl does not support HTTP3')
if not pin.exists():
    with pin.open('x') as f:
        os.chmod(pin, 0o600)
        f.write(image + '\n')
print('HTTP3_CLIENT_PINNED: ' + image)
PYCODE
rule=$(mktemp)
trap 'rm -f "$rule"' EXIT
printf '%s\n' 'ac-ci-builder ALL=(root) NOPASSWD: /usr/local/sbin/ac-nginx-deploy *' > "$rule"
visudo -cf "$rule"
install -d -o root -g root -m 0700 /var/lib/ac-nginx/deployments
install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0644 "$base/ac_nginx_release.py" /usr/local/libexec/ac_nginx_release.py
install -o root -g root -m 0755 "$base/ac-nginx-config-tools" /usr/local/libexec/ac-nginx-config-tools
install -o root -g root -m 0755 "$base/../verify-inspect.py" /usr/local/libexec/ac-nginx-verify-inspect
install -o root -g root -m 0755 "$base/ac-nginx-config-prepare" /usr/local/sbin/ac-nginx-config-prepare
install -o root -g root -m 0755 "$base/ac-nginx-deploy" /usr/local/sbin/ac-nginx-deploy
install -o root -g root -m 0440 "$rule" /etc/sudoers.d/ac-nginx-deploy
visudo -c
echo 'INSTALLED: ac-nginx-deploy; no deployment performed'
