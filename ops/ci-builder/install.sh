#!/bin/sh
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run as root' >&2; exit 1; }
[ "$(id -u ac-ci-builder)" = 1001 ]
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 - "$base/ac-ci-builder-store" <<'PY'
import ast, os, pathlib, pwd, stat, sys
ast.parse(pathlib.Path(sys.argv[1]).read_text())
gid = pwd.getpwnam('ac-ci-builder').pw_gid
root = pathlib.Path('/var/lib/ac-ci-builder/.local/state/ac-ci-builder')
for p in list(reversed(root.parents)) + [root]:
    if p.is_symlink():
        raise SystemExit('Refusing symlink: ' + str(p))
    if not p.exists():
        p.mkdir(mode=0o700)
        os.chown(p, 1001, gid)
    s = p.stat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid not in (0, 1001) or s.st_mode & 0o022:
        raise SystemExit('Unsafe directory: ' + str(p))
os.chown(root, 1001, gid)
os.chmod(root, 0o700)
for name in ('store.lock', 'nginx.lock', 'php.lock', 'postgresql.lock'):
    path = root / name
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        s = path.lstat()
        if not stat.S_ISREG(s.st_mode) or s.st_uid != 1001 or s.st_mode & 0o077:
            raise SystemExit('Unsafe existing lock: ' + str(path))
    else:
        os.fchown(fd, 1001, gid)
        os.close(fd)
PY
temporary=$(mktemp /usr/local/bin/.ac-ci-builder-store.XXXXXXXX)
trap 'rm -f "$temporary"' EXIT
install -o root -g root -m 0755 "$base/ac-ci-builder-store" "$temporary"
mv -f "$temporary" /usr/local/bin/ac-ci-builder-store
echo 'BUILDER_TOOLS_INSTALLED: activation flag unchanged; no cleanup performed'
