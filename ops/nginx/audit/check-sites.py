#!/usr/bin/python3
"""Read-only HTTPS survey; no container or service changes."""
import argparse
import concurrent.futures
import datetime
import json
import pathlib
import re
import subprocess
import sys

DOMAINS = ['ci.belov.email', 'belov.email', 'alteredcarbon.company',
           'alteredcarbon.store', 'carbonblog.org', 'nitropropen.com',
           'nitropropene.com', 'openmailserver.net']
ADDRESSES = [('IPv4', '93.115.20.205'), ('IPv6', '2a0c:b9c0:f:433c::1')]


def check(curl, domain, family, address, protocol):
    resolved = '[' + address + ']' if family == 'IPv6' else address
    command = [curl, '-q', '--noproxy', '*', '--connect-timeout', '5', '--max-time', '12',
               '--ipv6' if family == 'IPv6' else '--ipv4',
               '--http3-only' if protocol == 'HTTP/3' else '--http1.1',
               '--resolve', domain + ':443:' + resolved, '-sS', '-o', '/dev/null',
               '-w', '%{http_code}\t%{http_version}\t%{remote_ip}\t%{ssl_verify_result}',
               'https://' + domain + '/']
    result = {'domain': domain, 'family': family, 'protocol': protocol, 'target': address}
    try:
        process = subprocess.run(command, capture_output=True, text=True, timeout=16)
        fields = process.stdout.strip().split('\t')
        result.update(curl_exit=process.returncode, error=process.stderr.strip())
        if len(fields) == 4:
            code, version, remote, verify = fields
            result.update(http_code=code, http_version=version, remote_ip=remote, tls_verify=verify)
        else:
            code, version, verify = '000', '', ''
        if process.returncode != 0 or verify != '0' or (protocol == 'HTTP/3' and version != '3'):
            result['status'] = 'FAIL'
        elif domain == 'ci.belov.email':
            result['status'] = 'PASS' if code == '200' else 'REVIEW'
        else:
            result['status'] = 'PASS' if re.fullmatch('[23][0-9]{2}', code) else 'REVIEW'
    except (OSError, subprocess.TimeoutExpired) as error:
        result.update(status='FAIL', error=str(error))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--curl', default='/usr/bin/curl', help='curl executable with optional HTTP/3 support')
    parser.add_argument('--output', type=pathlib.Path, help='write JSON report (refuses overwrite)')
    parser.add_argument('domains', nargs='*', help='optional replacement domain list')
    args = parser.parse_args()
    domains = args.domains or DOMAINS
    if any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', d) for d in domains):
        parser.error('invalid domain name')
    if args.output and args.output.exists():
        parser.error('output already exists; choose a new filename')
    version = subprocess.check_output([args.curl, '-q', '--version'], text=True, timeout=5)
    help_text = subprocess.check_output([args.curl, '-q', '--help', 'all'], text=True, timeout=5)
    features = next((line.split(':', 1)[1].split() for line in version.splitlines() if line.startswith('Features:')), [])
    h3 = 'HTTP3' in features and '--http3-only' in help_text
    print(version.splitlines()[0], flush=True)
    print('Direct HTTPS survey: certificate verification enabled; redirects not followed.', flush=True)
    if not h3:
        print('SKIP: HTTP/3 — this curl does not support HTTP3 + --http3-only.', flush=True)
    jobs = [(domain, family, address, protocol) for domain in domains
            for family, address in ADDRESSES
            for protocol in (['HTTPS/TCP', 'HTTP/3'] if h3 else ['HTTPS/TCP'])]
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(check, args.curl, *job) for job in jobs]
        for future in futures:
            row = future.result()
            results.append(row)
            print('{status:6} {family:4} {protocol:9} {domain:25} HTTP={http_code} version={http_version}'.format(
                **dict({'http_code':'---', 'http_version':'---'}, **row)), flush=True)
            if row.get('error'):
                print('       ' + row['error'].replace('\n', ' ')[:600], flush=True)
    counts = {s: sum(row['status'] == s for row in results) for s in ('PASS', 'REVIEW', 'FAIL')}
    report = {'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'curl': version.strip(), 'http3_tested': h3, 'results': results, 'counts': counts}
    if args.output:
        with args.output.open('x') as output:
            json.dump(report, output, indent=2)
            output.write('\n')
        print('Report: ' + str(args.output))
    print('SUMMARY: ' + ' '.join(str(k)+'='+str(v) for k,v in counts.items()) + ' HTTP3=' + ('TESTED' if h3 else 'SKIPPED'))
    return 1 if counts['FAIL'] or counts['REVIEW'] else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, subprocess.SubprocessError) as error:
        print('AUDIT_ERROR: ' + str(error), file=sys.stderr)
        sys.exit(2)
