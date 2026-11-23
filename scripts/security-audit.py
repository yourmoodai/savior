"""Audit resolved dependencies; missing reports and scanner failures block verification."""

import argparse
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / '.cache/security/current'
PIP_AUDIT = '2.10.1'


def validate_report(ecosystem, report):
    if not isinstance(report, dict):
        return False
    if ecosystem == 'npm':
        return (not report.get('error')
                and report['metadata']['vulnerabilities']['total'] == 0
                and report['vulnerabilities'] == {})
    if ecosystem == 'rust':
        return (report['vulnerabilities']['count'] == 0
                and report['vulnerabilities']['list'] == []
                and report['settings']['ignore'] == []
                and report['settings']['severity'] is None
                and report['settings']['target_arch'] == []
                and report['settings']['target_os'] == []
                and not report.get('warnings', {}).get('unsound'))
    if ecosystem == 'python':
        dependencies = report['dependencies']
        return bool(dependencies) and all(
            not item.get('skip_reason') and item['vulns'] == []
            for item in dependencies)
    if ecosystem == 'container':
        return bool(report['Metadata']) and all(
            not result.get('Vulnerabilities') for result in report['Results'])
    raise ValueError('unknown audit ecosystem')


def run_audit(name, ecosystem, command):
    REPORTS.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    (REPORTS / (name + '.json')).write_text(result.stdout)
    (REPORTS / (name + '.log')).write_text(result.stderr)
    try:
        clean = validate_report(ecosystem, json.loads(result.stdout))
    except (ValueError, KeyError, TypeError):
        clean = False
    if result.returncode != 0 or not clean:
        print(f'{name}: audit failed; see {REPORTS / (name + ".json")}')
        return False
    if ecosystem == 'rust':
        for warning in json.loads(result.stdout).get('warnings', {}).get('unmaintained', []):
            print(f'{name}: upstream maintenance warning: {warning["package"]["name"]}')
    print(f'{name}: no known vulnerabilities')
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('ecosystem', choices=['all', 'npm', 'rust', 'python', 'container'])
    parser.add_argument('--image', default='savior-release-tools')
    args = parser.parse_args()
    audits = []
    if args.ecosystem in ('all', 'npm'):
        for name, prefix in [('npm', []), ('host', ['--prefix', 'tools/host'])]:
            audits.append((name, 'npm', ['npm', *prefix, 'audit', '--json', '--audit-level=low',
                                        '--include=dev', '--include=optional', '--include=peer']))
    if args.ecosystem in ('all', 'rust'):
        audits.append(('rust', 'rust', ['cargo', 'audit', '--json', '--deny', 'unsound']))
    if args.ecosystem in ('all', 'python'):
        for name, requirements in [
            ('reviewer', 'tools/adversarial_reviewer/requirements.txt'),
            ('coreml', 'scripts/requirements-coreml.txt'),
            ('windows', 'tools/host/requirements-windows.txt'),
        ]:
            audits.append((name, 'python', [
                'uvx', '--from', f'pip-audit=={PIP_AUDIT}', 'pip-audit',
                '--strict', '--disable-pip', '--require-hashes',
                '--format', 'json', '-r', requirements,
            ]))
    if args.ecosystem in ('all', 'container'):
        audits.append(('container', 'container', [
            'trivy', 'image', '--config', '/dev/null', '--ignorefile', '/dev/null',
            '--scanners', 'vuln', '--format', 'json',
            '--exit-code', '1', args.image,
        ]))
    success = True
    for audit in audits:
        try:
            success = run_audit(*audit) and success
        except OSError as error:
            print(f'{audit[0]}: scanner unavailable: {error}')
            success = False
    raise SystemExit(0 if success else 1)


if __name__ == '__main__':
    main()
