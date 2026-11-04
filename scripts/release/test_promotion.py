"""Reject stale release evidence and racing promotions without publishing."""
import unittest
from promote import qualified_run, advance


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.revision = 'a'*40
        self.run = {'workflow_id':12,'head_sha':self.revision,'head_branch':'v0.2.0',
                    'event':'push','status':'completed','conclusion':'success','run_attempt':2,
                    'repository':{'full_name':'example/project'}}
        names = ['gate','package','publish'] + [f'{job} ({platform})'
            for job in ('installed','published-download') for platform in ('macos-14','ubuntu-24.04','windows-2022')]
        names += [f'build ({platform}, {target})' for platform,target in (
            ('macos-14','darwin-arm64'),('ubuntu-24.04','linux-x64'),('windows-2022','win32-x64'))]
        self.jobs = {'total_count':len(names),'jobs':[{'name':name,'conclusion':'success'} for name in names]}
        self.refs = [{'ref':'refs/heads/marketplace','object':{'sha':'b'*40}}]
        self.writes = []

    def api(self,endpoint,method='GET',payload=None):
        if method!='GET':
            self.writes.append((endpoint,payload))
            return {'data':{'createCommitOnBranch':{'commit':{'oid':'c'*40}}},'sha':'c'*40}
        if endpoint.endswith('/workflows/release.yml'):
            return {'id':12}
        if '/attempts/2/jobs?' in endpoint:
            return self.jobs
        if '/actions/runs/' in endpoint:
            return self.run
        if '/matching-refs/' in endpoint:
            return self.refs
        self.fail(endpoint)

    def test_current_attempt_and_complete_native_matrix_required(self):
        self.assertEqual(qualified_run('example/project',7,self.revision,'v0.2.0',self.api),2)
        for field,value in [('workflow_id',13),('head_sha','d'*40),('event','workflow_dispatch'),
                            ('head_branch','main'),('status','in_progress'),('conclusion','failure')]:
            with self.subTest(field=field):
                previous = self.run[field]
                self.run[field] = value
                with self.assertRaises(ValueError):
                    qualified_run('example/project',7,self.revision,'v0.2.0',self.api)
                self.run[field] = previous
        for status in ('skipped','cancelled','failure',None):
            self.jobs['jobs'][-1]['conclusion'] = status
            with self.assertRaises(ValueError):
                qualified_run('example/project',7,self.revision,'v0.2.0',self.api)
        self.jobs['jobs'].pop()
        with self.assertRaises(ValueError):
            qualified_run('example/project',7,self.revision,'v0.2.0',self.api)

    def test_stale_approval_cannot_overwrite_marketplace(self):
        for head in (None,'d'*40):
            with self.assertRaisesRegex(ValueError,'changed_since_approval'):
                advance('example/project','0.2.0',b'{}',{},head,self.api)
        self.assertEqual(self.writes,[])
        advance('example/project','0.2.0',b'{}',{},'b'*40,self.api)
        self.assertEqual(len(self.writes),1)
        change = self.writes[0][1]['variables']['input']
        self.assertEqual(change['expectedHeadOid'],'b'*40)
        self.assertEqual([a['path'] for a in change['fileChanges']['additions']],['marketplace.json','promotion.json'])

    def test_create_is_compare_and_create_and_graphql_errors_fail(self):
        self.refs = []
        advance('example/project','0.2.0',b'{}',{},None,self.api)
        self.assertEqual(self.writes[-1][0],'repos/example/project/git/refs')
        self.assertEqual(self.writes[-1][1]['ref'],'refs/heads/marketplace')
        self.assertFalse(any('force' in payload for _,payload in self.writes))
        self.refs = [{'ref':'refs/heads/marketplace','object':{'sha':'b'*40}}]
        def rejected(endpoint,method='GET',payload=None):
            return {'errors':[{'message':'changed head'}]} if method=='POST' else self.api(endpoint)
        with self.assertRaisesRegex(ValueError,'update_rejected'):
            advance('example/project','0.2.0',b'{}',{},'b'*40,rejected)
