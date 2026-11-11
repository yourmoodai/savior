import os
import json
import http.client
import http.server
import threading
from pathlib import Path
import subprocess
import sys
import unittest
import importlib.util
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location('installed', Path(__file__).resolve().parents[1] / 'test-installed-mod.py')
installed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installed)


class InstalledTerminalTests(unittest.TestCase):
    def test_token_counting_is_observed_but_is_not_a_submission(self):
        installed.received.clear()
        installed.submissions.clear()
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), installed.Provider)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            body = {'messages': [{'role': 'user', 'content': 'You ya ya ya, keep the test.'}]}
            for path in ('/v1/messages/count_tokens', '/v1/messages?beta=true'):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                connection.request('POST', path, json.dumps(body), {'x-api-key': 'synthetic-local-key'})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                connection.close()
                self.assertEqual(len(installed.submissions), 0 if 'count_tokens' in path else 1)
            self.assertEqual(len(installed.received), 2)
        finally:
            server.shutdown()
            server.server_close()
            installed.received.clear()
            installed.submissions.clear()

    def test_original_marker_is_rejected_outside_the_user_message(self):
        self.assertTrue(installed.contains_original({'metadata': {'draft': 'You IDIOT'}}))
        self.assertTrue(installed.contains_original({'messages': [{'text': 'idiot'}]}))
        self.assertTrue(installed.contains_original({'idiot': 'key leak'}))
        self.assertTrue(installed.contains_original(json.loads('{"text":"\\u0069diot"}')))
        self.assertFalse(installed.contains_original({'messages': [{'text': 'You ya ya ya, keep the test.'}], 'tokens': 12}))

    def test_host_cache_preserves_only_the_candidate_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            plugin, home = root / 'plugin', root / 'home'
            prepared = plugin / 'cache'
            self.assertEqual(installed.host_cache_environment(plugin, home),
                             {'XDG_CACHE_HOME': str(home / 'cache'), 'LOCALAPPDATA': str(home / 'cache')})
            self.assertEqual(installed.host_cache_environment(plugin, home, str(prepared)),
                             {'XDG_CACHE_HOME': str(prepared), 'LOCALAPPDATA': str(prepared)})
            with self.assertRaisesRegex(ValueError, 'unsafe_host_cache'):
                installed.host_cache_environment(plugin, home, str(root / 'user-cache'))

    def test_pane_labels_accept_cursor_positioned_word_gaps(self):
        self.assertTrue(installed.ui_has_text(b'enable\x1b[1Clive metrics and sampled local judge',
                                              'enable live metrics and sampled local judge'))
        self.assertTrue(installed.ui_has_text(b'disable\x1b[1Clive\x1b[1Cmetrics', 'disable live metrics'))
        self.assertFalse(installed.ui_has_text(b'enable live metrics', 'disable live metrics'))
        self.assertFalse(installed.ui_has_text(b'enable live metrics',
                                               'enable live metrics and sampled local judge'))

    def test_draft_readiness_requires_the_latest_prompt_marker(self):
        self.assertTrue(installed.draft_visible('❯ You\u00a0idiot, keep it.'.encode(), 'You idiot, keep it.'))
        self.assertTrue(installed.draft_visible('❯ You \x1b[31midiot,\x1b[0m keep it.'.encode(), 'You idiot, keep it.'))
        self.assertTrue(installed.draft_visible('❯You\x1b[1Cidiot,\x1b[1Ckeep\x1b[1Cit.'.encode(), 'You idiot, keep it.'))
        self.assertFalse(installed.draft_visible('❯ You idiot, keep it.\n❯ '.encode(), 'You idiot, keep it.'))
        self.assertFalse(installed.draft_visible(b'You idiot, keep it.', 'You idiot, keep it.'))
        self.assertFalse(installed.draft_visible('❯ newer draft'.encode(), 'You idiot, keep it.'))

    def test_provider_requires_exact_user_text_not_a_substring(self):
        self.assertTrue(installed.provider_has_text({'messages':[{'role':'user','content':'expected'}]}, 'expected'))
        self.assertTrue(installed.provider_has_text({'messages':[{'role':'user','content':[{'type':'text','text':'expected'}]}]}, 'expected'))
        self.assertFalse(installed.provider_has_text({'messages':[{'role':'user','content':'unexpected'}]}, 'expected'))
        self.assertFalse(installed.provider_has_text({'messages':[{'role':'assistant','content':'expected'}]}, 'expected'))

    @unittest.skipUnless(os.name == 'posix', 'requires a Unix PTY')
    def test_close_unblocks_an_active_reader(self):
        root = Path(__file__).resolve().parents[2]
        # Isolate the close call so a regression fails within a bounded deadline.
        code = '''
import importlib.util
import os
import sys
import time
sys.path.insert(0, 'scripts')
spec = importlib.util.spec_from_file_location('installed', 'scripts/test-installed-mod.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
terminal = module.Terminal([sys.executable, '-c', 'import time; print("ready", flush=True); time.sleep(30)'], os.getcwd(), dict(os.environ))
deadline = time.monotonic() + 5
while b'ready' not in terminal.read():
    assert time.monotonic() < deadline
terminal.close()
assert not terminal.alive()
'''
        subprocess.run([sys.executable, '-c', code], cwd=root, check=True, timeout=10)
