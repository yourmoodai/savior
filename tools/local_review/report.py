"""Read only the verdict from a trusted run's artifact, without extracting files."""
import io
import json
import sys
import zipfile

def claims(data, head, quote_id):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        name = 'report.json' if 'report.json' in archive.namelist() else 'preflight.json'
        if archive.namelist().count(name) != 1 or archive.getinfo(name).file_size > 64_000_000:  # 64 MB
            raise ValueError('missing or oversized verdict')
        report = json.loads(archive.read(name))
        if report['commit_id'] != head or report['quote']['id'] != quote_id:
            raise ValueError('report revision or quote mismatch')
        if 'findings' in report:
            if not isinstance(report['findings'], list):
                raise ValueError('invalid findings')
            return report['findings']
        if (report.get('blocked_reason') == 'cost_limit'
                and set(report) == {'commit_id', 'quote', 'budget', 'blocked_reason'}
                and archive.namelist().count('preflight.json') == 1
                and archive.getinfo('preflight.json').file_size <= 64_000_000
                and report == json.loads(archive.read('preflight.json'))):
            return []
        raise ValueError('missing findings or preflight evidence')

if __name__ == '__main__':
    print(json.dumps(claims(sys.stdin.buffer.read(64_000_001), *sys.argv[1:])))
