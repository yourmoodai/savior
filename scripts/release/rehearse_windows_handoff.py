"""Exercise the package verifier against the fixture received from Windows."""
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from windows_handoff import digest, verify


def rehearse(directory, expected):
    stage = directory/'stage'
    receipt = directory/'verification.json'
    def check():
        return verify(stage, receipt, expected, 'fixture')

    def reject(case):
        try:
            check()
        except ValueError:
            print('rejected '+case)
        else:
            raise AssertionError('accepted '+case)

    record = check()
    binary = stage/'fixture.exe'
    original = binary.read_bytes()
    original_receipt = receipt.read_bytes()
    with tempfile.TemporaryDirectory() as temp:
        binary.replace(Path(temp)/'fixture.exe')
        reject('missing file')
    binary.write_bytes(original)
    extra = stage/'extra.exe'
    extra.write_bytes(original)
    reject('extra file')
    extra.unlink()
    binary.write_bytes(original[:-1]+bytes([original[-1] ^ 1]))
    reject('changed byte')
    record['files']['fixture.exe'] = digest(binary)
    receipt.write_text(json.dumps(record))
    reject('replacement file and record')
    receipt.write_bytes(original_receipt)
    binary.write_bytes(original)
    for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA'):
        with patch.dict(os.environ, {key: 'different'}):
            reject(key.lower())
    return check()


if __name__ == '__main__':
    evidence = rehearse(Path('windows-handoff'), os.environ['WINDOWS_RECORD_SHA256'])
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
        print('Verified signed fixture and rejected all substitutions.\n\n```json', file=summary)
        print(json.dumps(evidence, indent=2), '\n```', file=summary)
