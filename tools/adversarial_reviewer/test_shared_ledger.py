"""Simulate independent workers on an object supporting conditional writes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import chdir, ExitStack
from io import BytesIO
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError
from budget import BudgetError, BudgetExhausted, Ledger
from shared_ledger import shared_snapshot
from host_binding import check_revision
from host_review import command
from qualify import cached_qualification
from seed_budget import main as seed_budget


class ObjectStore:
    def __init__(self, data):
        self.data = data
        self.version = 1
        self.lock = threading.Lock()
        self.race = None

    def get_object(self, **args):
        with self.lock:
            return {'Body':BytesIO(self.data),'ContentLength':len(self.data),'ETag':str(self.version),
                    'Metadata':{'repository':'1418413891'}}

    def put_object(self, *, Body, IfMatch, **kwargs):
        if self.race:
            self.race.wait(5)
        with self.lock:
            if IfMatch != str(self.version):
                raise ClientError({'Error':{'Code':'PreconditionFailed'}},'PutObject')
            self.data = Body
            self.version += 1


class SharedLedgerTests(unittest.TestCase):
    def test_competing_writers_and_unknown_dispatch_retain_caps(self):
        with tempfile.TemporaryDirectory() as folder:
            initial = Path(folder)/'seed.db'
            Ledger(initial)
            store = ObjectStore(initial.read_bytes())
            model = {'pricing':{'input_nanodollars':1,'output_nanodollars':1}}
            env = {'SAVIOR_BEDROCK_LEDGER_BUCKET':'test','REVIEW_BUDGET_LEDGER':'local',
                   'REVIEW_COST_POLICY':'true','PR_NUMBER':'17','GITHUB_REPOSITORY':'yourmoodai/savior'}
            with patch.dict(os.environ,env), patch('shared_ledger.shared_snapshot',
                    side_effect=lambda bucket: shared_snapshot(bucket,store)):
                def reserve(identity):
                    return Ledger('local').reserve(1_050_000_000,0,'review',identity,model=model)
                store.race = threading.Barrier(2)
                with ThreadPoolExecutor(2) as workers:
                    calls = [workers.submit(reserve, identity) for identity in ('host','runner')]
                    keys = [f.result() for f in calls if f.exception() is None]
                    conflicts = [f.exception() for f in calls if f.exception() is not None]
                self.assertEqual(len(keys),1)
                self.assertIsInstance(conflicts[0],BudgetError)
                store.race = None
                ledger = Ledger('local')
                self.assertEqual(ledger.policy_budget()['pr_remaining_nanodollars'],950_000_000)
                with self.assertRaises(BudgetExhausted):
                    reserve('retry')
                ledger.reconcile(keys[0],{'usage':{'inputTokens':1,'outputTokens':0}},0)
                self.assertEqual(Ledger('local').policy_budget()['pr_remaining_nanodollars'],1_999_999_999)

    def test_missing_or_damaged_authority_never_creates_a_new_ledger(self):
        store = ObjectStore(b'corrupt')
        with self.assertRaises(BudgetError), shared_snapshot('test',store):
            self.fail('invalid ledger opened')
        self.assertEqual(store.version,1)

    def test_seed_requires_archives_and_retired_inference(self):
        with tempfile.TemporaryDirectory() as folder, chdir(folder), patch('seed_budget.get_session') as session:
            def seed():
                with patch.dict(os.environ,SAVIOR_BEDROCK_LEDGER_BUCKET='test'):
                    seed_budget()
            with self.assertRaisesRegex(ValueError,'historical spending'):
                seed()
            session.assert_not_called()
            directory = Path('review-result/previous-ledgers')
            directory.mkdir(parents=True)
            (directory/'1.zip').touch()
            iam, s3 = Mock(), Mock()
            session.return_value.create_client.side_effect = lambda name, **kwargs: {'iam':iam,'s3':s3}[name]
            deny = {'Effect':'Deny','Action':'bedrock:InvokeModel','Resource':'*'}
            iam.get_role_policy.return_value = {'PolicyDocument':{'Statement':[{**deny,'Condition':{}}]}}
            with self.assertRaisesRegex(ValueError,'retire legacy'):
                seed()
            s3.put_object.assert_not_called()
            iam.get_role_policy.return_value = {'PolicyDocument':{'Statement':[deny]}}
            with self.assertRaises(BudgetError):
                seed()
            s3.put_object.assert_not_called()

    def test_revision_moves_and_unknown_quote_cannot_dispatch(self):
        pr = {'base':{'sha':'a'*40},'head':{'sha':'b'*40}}
        with patch.dict(os.environ,PR_NUMBER='1',BASE_SHA='a'*40,HEAD_SHA='b'*40):
            for side in ('base','head'):
                with patch('host_binding.checked_pr',return_value={**pr,side:{'sha':'c'*40}}):
                    with self.assertRaisesRegex(ValueError,'revisions changed'):
                        check_revision()
        with tempfile.TemporaryDirectory() as directory, patch('host_review.STATE',Path(directory)), \
             patch('host_review.checked_pr',return_value=pr),patch('host_review.fetch_objects'), \
             patch('host_review.check_deployment'),patch.dict(os.environ,{}):
            with self.assertRaises(FileNotFoundError):
                command(1,'c'*64)

    def test_quote_request_and_fresh_authorization_after_qualification(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            state = Path(directory)
            git_dir = state/'repository'
            git_dir.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-C', str(git_dir), *args], text=True).strip()
            git('init', '--quiet', '--template=')
            revisions = []
            for value in ('one', 'two'):
                (git_dir/'sample.md').write_text(value+'\n')
                git('add','sample.md')
                revisions.append(git('-c','commit.gpgsign=false','-c','user.name=test','-c',
                    'user.email=test@example.org','commit-tree',git('write-tree'),
                    *(['-p',revisions[0]] if revisions else []),'-m',value))
            pr = {'base':{'sha':revisions[0]}, 'head':{'sha':revisions[1]}}
            seed = state/'seed.db'
            Ledger(seed)
            store = ObjectStore(seed.read_bytes())
            stack.enter_context(patch.dict(os.environ, AWS_REGION='us-east-1', GIT_DIR=str(git_dir/'.git'),
                SAVIOR_BEDROCK_LEDGER_BUCKET='test', REVIEW_COST_POLICY='true'))
            stack.enter_context(patch('host_review.STATE',state))
            stack.enter_context(patch('host_review.checked_pr',return_value=pr))
            stack.enter_context(patch('host_binding.checked_pr',return_value=pr))
            stack.enter_context(patch('host_review.check_deployment'))
            stack.enter_context(patch('host_review.fetch_objects'))
            stack.enter_context(patch('shared_ledger.shared_snapshot',
                                      side_effect=lambda bucket: shared_snapshot(bucket,store)))
            # Exercise the real executor in-process with a synthetic qualification.
            # Git and both evidence planners run against the two fixture commits.
            run = subprocess.run
            def invoke(args, **kwargs):
                if args[0] == 'node' and str(args[1]).endswith('/test_status.cjs'):
                    Path(args[-1]).write_text(json.dumps({'head':revisions[1],'runs':[]}))
                    return subprocess.CompletedProcess(args,0)
                if len(args) > 1 and str(args[1]).endswith('/run_ci.py'):
                    with patch.dict(os.environ):
                        runpy.run_path(args[1],run_name='__main__')
                    return subprocess.CompletedProcess(args,0)
                if '--plan-file' in args and str(args[1]).endswith('/adversarial_review.py'):
                    from adversarial_review import main
                    with patch.object(sys,'argv',args[1:]):
                        return subprocess.CompletedProcess(args,main())
                return run(args,**kwargs)
            qualified = {'qualified':True, 'strategy':'baseline', 'cases':[{'passed':True}],
                         'comparison':{'baseline':{'passed':True}}}
            with patch('subprocess.run',side_effect=invoke), patch('qualify._qualify',return_value=qualified) as model:
                first = command(1,None)
                self.assertEqual(first['phase'],'qualification_required')
                report = command(1,first['quote_id'])
                self.assertEqual(report['blocked_reason'],'assessment_quote_required')
                self.assertFalse(report['complete'])
                second = command(1,None)
                self.assertEqual(second['phase'],'review')
                with self.assertRaisesRegex(ValueError,'fresh quote'):
                    command(1,first['quote_id'])
                identity, _ = cached_qualification()
                (state/'cache'/(identity+'.json')).unlink()
                with self.assertRaisesRegex(ValueError,'fresh quote'):
                    command(1,second['quote_id'])
                with self.assertRaises(FileExistsError):
                    command(1,first['quote_id'])
                model.assert_called_once()
                from result_cache import put
                put(identity,qualified)
                answer = json.dumps({'abstention':'','rationale':'The change updates the fixture paragraph.','findings':[]})
                envelope = {'usage':{'inputTokens':10,'outputTokens':5},'stopReason':'end_turn',
                            'output':{'message':{'content':[{'text':answer}]}}}
                with patch('bedrock_client.invoke',return_value=envelope) as transport, \
                     patch('chat_transport.invoke_chat',side_effect=AssertionError('unexpected model transport')):
                    reviewed = command(1,second['quote_id'])
                    self.assertTrue(reviewed['complete'],reviewed)
                    self.assertEqual(reviewed['disposition'],'APPROVE')
                    self.assertEqual(reviewed['completed_chunks'],reviewed['total_chunks'])
                    self.assertEqual(transport.call_count,3)
                    with self.assertRaises(FileExistsError):
                        command(1,second['quote_id'])
                    self.assertEqual(transport.call_count,3)
