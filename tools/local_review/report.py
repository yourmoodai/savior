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
        if name == 'preflight.json' and report['blocked_reason'] == 'cost_limit':
            return []
        return report['findings']

if __name__ == '__main__':
    print(json.dumps(claims(sys.stdin.buffer.read(64_000_001), *sys.argv[1:])))
