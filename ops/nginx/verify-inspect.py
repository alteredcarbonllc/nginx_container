#!/usr/bin/env python3
"""Reject unexpected drift before a production supervisor migration."""
import json
import sys
from ac_nginx_release import validate_config, config_mount

def require(condition, message):
    if not condition:
        raise SystemExit('Unexpected container configuration: ' + message)

require(not sys.argv[1:], 'arguments')
c = json.load(sys.stdin)[0]
require(c['Config']['Entrypoint'] == ['/entrypoint.sh'], 'entrypoint')
require(c['Config']['Cmd'] == ['nginx', '-g', 'daemon off;'], 'command')
mounts = {m['Destination'].rstrip('/'): (m['Source'].rstrip('/'), m['RW'], m['Type']) for m in c['Mounts']}
config = config_mount(c)
validate_config(config)
expected_mounts = {
    '/etc/nginx': (config, False, 'bind'),
    '/usr/etc': ('/root/git/containers_etc/nginx_container_openmailserver.net/usr/etc', True, 'bind'),
    '/var/www': ('/var/volumes/data/nginx_container_openmailserver.net/var/www', True, 'bind'),
    '/var/log': ('/var/volumes/log/nginx_container_openmailserver.net/var/log', True, 'bind'),
    '/etc/letsencrypt': ('/var/volumes/data/letsencrypt_container_openmailserver.net/etc/letsencrypt', True, 'bind'),
    '/run/secrets/nginx': ('/etc/ac/secrets/nginx', False, 'bind'),
}
require(mounts == expected_mounts and len(c['Mounts']) == 6, 'mounts/access modes')
networks = c['NetworkSettings']['Networks']
require(set(networks) == {'ac_network'}, 'networks')
n = networks['ac_network']
require(n['IPAddress'] == '10.89.1.224', 'IPv4')
require(n['GlobalIPv6Address'] == 'fd00:10:89:1::224', 'IPv6')
require(n['MacAddress'].lower() == 'ce:a5:c4:01:f3:cb', 'MAC')
ports = c['HostConfig']['PortBindings']
require(set(ports) == {f'{p}/{proto}' for p in (80,443) for proto in ('tcp','udp')}, 'ports')
for key, bindings in ports.items():
    port = key.split('/')[0]
    require(len(bindings) == 2 and {(b['HostIp'], b['HostPort']) for b in bindings} == {
        ('93.115.20.205', port), ('2a0c:b9c0:f:433c::1', port)}, 'port bindings: ' + key)
print('INSPECT_OK')
