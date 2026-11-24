import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prepare_windows_handoff import prepare
from rehearse_windows_handoff import rehearse
from windows_handoff import BINARIES, NODE, RELEASE_FILES, digest, inventory, verify


class HandoffTests(unittest.TestCase):
    def test_producer_and_consumer(self):
        for kind in ('release', 'fixture'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                stage = directory/'stage'
                paths = RELEASE_FILES if kind == 'release' else {'fixture.exe'}
                for name in paths:
                    path = stage/name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'unsigned fixture')
                env = {k: '1' for k in ('GITHUB_RUN_ATTEMPT', 'GITHUB_RUN_ID', 'GITHUB_REPOSITORY_ID',
                                        'GITHUB_WORKFLOW_REF', 'GITHUB_REPOSITORY')}
                env.update(GITHUB_SHA='a'*40, GITHUB_OUTPUT=str(directory/'output'),
                           SAVIOR_RELEASE_CONFIG=json.dumps({'identities': {'node_windows':'1'*64}}))
                if kind == 'release':
                    files = inventory(stage, kind)
                    del files['stage.json']
                    (stage/'stage.json').write_text(json.dumps({'schema':1,'target':'win32-x64',
                        'version':json.loads(Path('package.json').read_bytes())['version'],
                        'revision':env['GITHUB_SHA'],'files':files}))
                signature = {'signer_sha256':'1'*64, 'timestamp_signer_sha256':'2'*64,
                             'publisher':'fixture','timestamp':'fixture'}
                def sign(paths):
                    for path in paths:
                        path.write_bytes(b'signed fixture')
                with patch.dict(os.environ, env), patch('prepare_windows_handoff.sign',side_effect=sign) as signer, \
                     patch('prepare_windows_handoff.subprocess.check_output',return_value=json.dumps(signature).encode()) as inspect:
                    prepare(directory,kind)
                    receipt = directory/'verification.json'
                    expected = (directory/'output').read_text().strip().split('=')[1]
                    self.assertEqual(expected,digest(receipt))
                    result = verify(stage, receipt, expected, kind)
                    if kind == 'fixture':
                        self.assertEqual(rehearse(directory,expected),result)
                    else:
                        self.assertEqual(set(result['signatures']),{*BINARIES,NODE})
                        self.assertEqual(signer.call_args.args[0],[stage/p for p in BINARIES])
                        self.assertEqual(inspect.call_args_list[0].args[0][-1],'1'*64)
                        (stage/NODE).write_bytes(b'different')
                        with self.assertRaisesRegex(ValueError,'invalid_unsigned_windows_stage'):
                            prepare(directory,kind)
                        signer.assert_called_once()
