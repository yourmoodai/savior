"""The offline gate must refuse developer hosts and restore CI networking on failure."""
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
import json
from pathlib import Path
from unittest.mock import patch
from native_acceptance import acceptance, offline_macos
from windows_identity import profile_eku


class OfflineGateTests(unittest.TestCase):
    def test_profile_certificate_and_signed_leaf_are_required_for_savior_only(self):
        eku = '1.3.6.1.4.1.311.97.123.456.789.101'
        config = {'windows': {'storetype':'TRUSTEDSIGNING'}, 'identities':{
            'apple':'a'*64, 'node_apple':'b'*64, 'node_windows':'c'*64, 'windows_profile_eku':eku}}
        self.assertEqual(profile_eku(config), eku)
        for invalid in ('1.3.6.1.5.5.7.3.3', '1.3.6.1.4.1.311.97.1.0', eku+'junk'):
            with self.assertRaisesRegex(ValueError, 'invalid_windows_profile_identity'):
                profile_eku({**config, 'identities':{**config['identities'], 'windows_profile_eku':invalid}})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root/'runtime-manifest.json'
            entry = {'asset':'savior.exe','signer_sha256':'d'*64}
            manifest.write_text(json.dumps({'targets':{'win32-x64':[entry]}}))
            with patch.dict(os.environ, {'SAVIOR_RELEASE_CONFIG':json.dumps(config)}), patch('native_acceptance.run') as verify:
                with acceptance(root, root, 'win32-x64', True):
                    pass
                savior, node = [c.kwargs['env'] for c in verify.call_args_list]
                self.assertEqual((savior['SAVIOR_NATIVE_PROFILE_EKU'],savior['SAVIOR_NATIVE_FINGERPRINT']), (eku,'d'*64))
                self.assertEqual((node['SAVIOR_NATIVE_PROFILE_EKU'],node['SAVIOR_NATIVE_FINGERPRINT']), ('','c'*64))
                del entry['signer_sha256']
                manifest.write_text(json.dumps({'targets':{'win32-x64':[entry]}}))
                with self.assertRaises(KeyError), acceptance(root, root, 'win32-x64', True):
                    self.fail('missing release-bound leaf accepted')

    def test_first_launch_precedes_policy_cache_population(self):
        events=[]
        @contextmanager
        def offline():
            events.append('blocked')
            yield
            events.append('restored')
        configuration=json.dumps({'identities':{key:'a'*64 for key in ('apple','windows','node_apple','node_windows')}})
        def command(*args,**_kwargs):
            events.append(args[0])
        with patch.dict(os.environ,{'SAVIOR_RELEASE_CONFIG':configuration}), \
                patch('native_acceptance.offline_macos',offline),patch('native_acceptance.run',side_effect=command), \
                patch('native_acceptance.apple_identity'):
            with acceptance(Path('/fixture'),Path('/release'),'darwin-arm64',True):
                events.append('first_launch')
        self.assertLess(events.index('blocked'),events.index('first_launch'))
        self.assertLess(events.index('first_launch'),events.index('/usr/sbin/spctl'))
        self.assertLess(events.index('first_launch'),events.index('/usr/bin/xcrun'))
        self.assertEqual(events[-1],'restored')

    def test_developer_and_self_hosted_machines_cannot_change_firewall(self):
        for environment in ({}, {'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'self-hosted'}):
            with patch.dict(os.environ,environment,clear=True),patch('native_acceptance.run') as command:
                with self.assertRaisesRegex(ValueError,'disposable_github_host'):
                    with offline_macos():
                        self.fail('network gate entered on developer host')
                command.assert_not_called()

    def test_native_failure_restores_rules_and_prior_enable_state(self):
        environment={'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted'}
        for enabled in (False,True):
            calls=[]
            def command(*args,**kwargs):
                calls.append((args,kwargs))
                return b'Status: Enabled' if enabled else b'Status: Disabled'
            with patch.dict(os.environ,environment,clear=True),patch('native_acceptance.run',side_effect=command), \
                    patch('native_acceptance.subprocess.run',return_value=subprocess.CompletedProcess([],1)):
                with self.assertRaisesRegex(RuntimeError,'native_policy_failed'):
                    with offline_macos():
                        self.assertTrue(any(kwargs.get('input')==b'set skip on lo0\nblock drop all\n' for _,kwargs in calls))
                        raise RuntimeError('native_policy_failed')
            self.assertIn((('sudo','/sbin/pfctl','-f','/etc/pf.conf'),{}),calls)
            self.assertEqual(any(args[-1]=='-d' for args,_ in calls),not enabled)

    def test_successful_external_connection_cannot_count_as_offline(self):
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted'},clear=True), \
                patch('native_acceptance.run',return_value=b'Status: Disabled') as command, \
                patch('native_acceptance.subprocess.run',return_value=subprocess.CompletedProcess([],0)):
            with self.assertRaisesRegex(ValueError,'network_block_failed'):
                with offline_macos():
                    self.fail('reachable network accepted')
            self.assertIn(('sudo','/sbin/pfctl','-f','/etc/pf.conf'),[c.args for c in command.call_args_list])
