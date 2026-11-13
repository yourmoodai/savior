"""Independent source dependency oracles and immutable-plan mutation checks."""
import copy
import difflib
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch
from collections import Counter

from adversarial_review import review_messages
from bedrock_client import estimate_input_units
from chunk_plan import anchors, render, manifest, parse
from source_graph import Graph, MemorySnapshot, digest, git_snapshots, SourceError, snapshots
from review_plan import pack, restore, require_feasible, admit, PlanningError, frozen_execution


def sources(old, new):
    return {'old': MemorySnapshot(old), 'new': MemorySnapshot(new)}


def difference(old, new):
    return ''.join('diff --git a/'+p+' b/'+p+'\n'+''.join(difflib.unified_diff(
        old.get(p, '').splitlines(True), new.get(p, '').splitlines(True), fromfile='a/'+p, tofile='b/'+p))
        for p in sorted(set(old)|set(new)) if old.get(p) != new.get(p))


def messages(units, support):
    return review_messages(render(units), sorted({u.path for u in units}), '', anchors(units),
                           supporting_context=support)


class SourceGraphTests(unittest.TestCase):
    def test_runtime_uncertainty_survives_planning_and_restore(self):
        examples = {
            'a.py': 'from pathlib import Path\nroot = Path(__file__).resolve()\ndef f(paths):\n    return any(p.startswith("site/") for p in paths)\n',
            'b.py': 'class C:\n    def method(self):\n        return 1\ndef f():\n    return C().method()\n',
            'c.mjs': 'const server = createServer(handler);\nawait new Promise(resolve => server.listen(0, resolve));\n',
            'd.py': 'import external as client\ndef f(client):\n    return client.method()\n',
            'e.py': 'import external as client\nclient = factory()\ndef f():\n    return client.method()\n',
        }
        for path, code in examples.items():
            with self.subTest(path=path):
                new = {path: code}
                snapshot, diff = sources({}, new), difference({}, new)
                for strategy in ('baseline', 'adaptive'):
                    planned = pack(diff, estimate_input_units, messages, snapshot, strategy)
                    require_feasible({strategy: manifest(planned)})
                    self.assertTrue(planned['chunks'][0]['analysis_limitations'])
                    self.assertIn('runtime', planned['chunks'][0]['support'])
                    restore(planned['review_plan'], diff, snapshot)
                    broken = copy.deepcopy(planned['review_plan'])
                    broken['chunks'][0]['analysis_limitations'] = []
                    broken['digest'] = digest({k:v for k,v in broken.items() if k != 'digest'})
                    with self.assertRaises(PlanningError):
                        restore(broken, diff, snapshot)

    def setUp(self):
        guard = patch('bedrock_client.invoke', side_effect=AssertionError('offline only'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_four_languages_resolve_unchanged_helpers_and_aliases(self):
        examples = [
            ('a.py', 'from helper import allowed as ok\ndef submit(x):\n    return ok(x)\n', 'helper.py', 'def allowed(x):\n    return x > 0\n'),
            ('a.rs', 'use crate::helper::allowed;\nfn submit(x:i32)->bool { allowed(x) }\n', 'helper.rs', 'fn allowed(x:i32)->bool { x > 0 }\n'),
            ('a.js', "import {allowed as ok} from './helper';\nexport function submit(x) { return ok(x); }\n", 'helper.js', 'export function allowed(x) { return x > 0; }\n'),
            ('a.ts', "import {ok} from './index';\nexport function submit(x:number) { return ok(x); }\n", 'index.ts', "export {allowed as ok} from './helper';\n")]
        for path, code, helper, dependency in examples:
            with self.subTest(path=path):
                files = {path: code, helper: dependency}
                if path.endswith('.ts'):
                    files['helper.ts'] = 'export function allowed(x:number) { return x > 0; }\n'
                graph = Graph(sources(files, files))
                roots = graph.module('new', path)
                closure, issues = graph.closure(roots)
                self.assertFalse(issues)
                self.assertIn(helper, {graph.nodes[n].path for n in closure})
                if path.endswith('.ts'):
                    self.assertIn('helper.ts', {graph.nodes[n].path for n in closure})

    def test_independent_functions_no_longer_require_entire_hunk(self):
        new = {'large.py': ''.join(f'def f_{i}():\n    return '+repr('x'*200)+'\n' for i in range(240))}
        diff, snapshot = difference({}, new), sources({}, new)
        for strategy in ('baseline', 'adaptive'):
            packed = pack(diff, estimate_input_units, messages, snapshot, strategy)
            self.assertGreater(len(packed['chunks']), 1)
            self.assertTrue(all(not c['omitted_support'] and c['verification_input_units'] <= 48000 for c in packed['chunks']))
            restore(packed['review_plan'], diff, snapshot)

    def test_cycles_terminate_and_dynamic_dispatch_remains_unresolved(self):
        files = {'a.py': 'from b import second\ndef first():\n    return second()\n',
                 'b.py': 'from a import first\ndef second():\n    return first()\n'}
        graph = Graph(sources(files, files))
        closure, issues = graph.closure(graph.module('new', 'a.py'))
        self.assertEqual(len(closure), 4)
        self.assertFalse(issues)
        for path, source in [('a.py','def f(x):\n    return getattr(x, name)()\n'), ('a.rs','fn f() { arbitrary!(); }')]:
            graph = Graph(sources({}, {path:source}))
            self.assertTrue(graph.closure(graph.module('new', path))[1])
        for code in ['export async function f(path) { return import(path); }',
                     'function f(path) { return require(path); }']:
            graph = Graph(sources({}, {'a.js': code}))
            self.assertTrue(graph.closure(graph.module('new', 'a.js'))[1])

    def test_comments_and_filename_strings_do_not_create_dependencies(self):
        files = {'a.py': '# import missing\ndef f():\n    return "from helper import secret"\n',
                 'helper.py': 'def secret():\n    return 1\n'}
        graph = Graph(sources({}, files))
        closure, issues = graph.closure(graph.module('new','a.py'))
        self.assertEqual({graph.nodes[n].path for n in closure}, {'a.py'})
        self.assertFalse(issues)

    def test_unicode_coordinates_and_plan_mutations(self):
        old, new = {'a.py':'def f():\n    return "é"\n'}, {'a.py':'def f():\n    return "你好"\n'}
        diff, snapshot = difference(old,new), sources(old,new)
        frozen = pack(diff,estimate_input_units,messages,snapshot,'adaptive')['review_plan']
        self.assertEqual(frozen, pack(diff,estimate_input_units,messages,snapshot,'adaptive')['review_plan'])
        for mutate in (lambda p:p['chunks'].append(p['chunks'][0]),
                       lambda p:p['chunks'][0]['units'][0].update(new=99),
                       lambda p:p['chunks'][0]['messages'][0].update(content='changed'),
                       lambda p:p['chunks'][0]['omitted_support'].append('missing')):
            changed=copy.deepcopy(frozen)
            mutate(changed)
            changed['digest']=digest({k:v for k,v in changed.items() if k!='digest'})
            with self.assertRaises(PlanningError):
                restore(changed,diff,snapshot)

    def test_oversized_closure_rejected_before_qualification(self):
        new={'a.py':'def f():\n    return '+repr('x'*60000)+'\n'}
        packed=pack(difference({},new),estimate_input_units,messages,sources({},new),'baseline')
        with patch('qualify.qualify') as qualify, patch('budget.Ledger.reserve') as reserve:
            with self.assertRaises(PlanningError):
                require_feasible({'baseline':manifest(packed)})
        qualify.assert_not_called()
        reserve.assert_not_called()

    def test_same_dependencies_fit_after_splitting_primary_evidence(self):
        for dynamic in (False, True):
            source = 'def f(callback):\n'+''.join(f'    value_{i} = "'+('x'*24)+'"\n' for i in range(300))
            source += '    return callback()\n' if dynamic else '    return value_299\n'
            new = {'a.py': source}
            diff, snapshot = difference({}, new), sources({}, new)
            for strategy in ('baseline', 'adaptive'):
                with self.subTest(dynamic=dynamic, strategy=strategy):
                    packed = pack(diff, estimate_input_units, messages, snapshot, strategy)
                    self.assertGreater(len(packed['chunks']), 1)
                    required = {tuple(c['required_evidence']) for c in packed['chunks']}
                    self.assertEqual(len(required), 1)
                    for chunk in packed['chunks']:
                        self.assertIn(source.rstrip(), chunk['support'])
                        self.assertLessEqual(chunk['input_tokens'], 48000)
                        self.assertLessEqual(chunk['verification_input_units'], 48000)
                    expected = Counter(a['id'] for a in anchors(parse(diff)))
                    actual = Counter(a['id'] for c in packed['chunks'] for a in anchors(c['units']))
                    self.assertEqual(actual, expected)
                    if dynamic:
                        self.assertTrue(all(c['analysis_limitations'] for c in packed['chunks']))
                    require_feasible({'plan': manifest(packed)})
                    restore(packed['review_plan'], diff, snapshot)

    def test_indivisible_source_and_subdivision_limits_preserve_coverage(self):
        for rows, limit, reason in [(1500, 512, 'required semantic closure exceeds request capacity'),
                                    (300, 1, 'planning subdivision limit exceeded')]:
            source = 'def f():\n'+''.join(f'    value_{i} = "'+('x'*24)+'"\n' for i in range(rows))
            new = {'a.py': source}
            diff, snapshot = difference({}, new), sources({}, new)
            measured = Mock(wraps=messages)
            with patch('review_plan.ASSESSMENT_LIMIT', limit):
                packed = pack(diff, estimate_input_units, measured, snapshot, 'baseline')
            self.assertTrue(any(reason in c['omitted_support'] for c in packed['chunks']))
            self.assertLessEqual(measured.call_count, 8)
            self.assertEqual(Counter(a['id'] for c in packed['chunks'] for a in anchors(c['units'])),
                             Counter(a['id'] for a in anchors(parse(diff))))
            with self.assertRaises(PlanningError):
                require_feasible({'plan': manifest(packed)})

    def test_shadowed_parameter_and_unrelated_declarations(self):
        files = {'a.py': 'def helper():\n    return 1\ndef f(helper):\n    return helper\n'}
        graph = Graph(sources({}, files))
        root = next(n for n in graph.module('new','a.py') if 'f' in graph.nodes[n].names)
        closure, issues = graph.closure([root])
        self.assertEqual(closure, [root])
        self.assertFalse(issues)

    def test_deleted_and_renamed_source_uses_old_revision(self):
        diff='diff --git a/old.py b/new.py\nrename from old.py\nrename to new.py\n--- a/old.py\n+++ b/new.py\n@@ -1,2 +1,2 @@\n def f():\n-    return helper()\n+    return 0\n'
        old={'old.py':'def f():\n    return helper()\ndef helper():\n    return 1\n'}
        new={'new.py':'def f():\n    return 0\n'}
        plan=pack(diff,estimate_input_units,messages,sources(old,new),'baseline')
        nodes=plan['review_plan']['nodes']
        self.assertTrue(any(n['path']=='old.py' and 'helper' in n['names'] for n in nodes.values()))

    def test_json_records_and_markdown_sections_have_bounded_ownership(self):
        files={'a.json':json.dumps([{'id':i,'value':'x'*200} for i in range(200)],indent=2)+'\n',
               'a.md':'# first\nhello\n# second\nworld\n'}
        graph=Graph(sources({},files))
        self.assertEqual(len(graph.module('new','a.json')),200)
        self.assertEqual(len(graph.module('new','a.md')),2)
        packed=pack(difference({},files),estimate_input_units,messages,sources({},files),'adaptive')
        self.assertTrue(all(not c['omitted_support'] for c in packed['chunks']))

    def test_syntax_error_and_missing_relative_import_abstain(self):
        for path,code in [('a.py','def broken(\n'),('a.js',"import x from './absent';\nexport function f(){ return x(); }\n")]:
            graph=Graph(sources({}, {path:code}))
            self.assertTrue(graph.closure(graph.module('new',path))[1])

    def test_removed_required_dependency_is_rejected(self):
        files={'a.py':'def helper():\n    return 1\ndef f():\n    return helper()\n'}
        diff=difference({},files); snapshot=sources({},files)
        frozen=pack(diff,estimate_input_units,messages,snapshot,'baseline')['review_plan']
        frozen['chunks'][0]['required_evidence'].pop()
        frozen['digest']=digest({k:v for k,v in frozen.items() if k!='digest'})
        with self.assertRaises(PlanningError): restore(frozen,diff,snapshot)

    def test_preflight_checks_feasibility_before_loading_qualification(self):
        import preflight
        with tempfile.TemporaryDirectory() as folder:
            def render_manifest(argv, **kwargs):
                Path(argv[-1]).write_text(json.dumps({'manifest': {'chunks': [{'omitted_support':['missing helper']}]}}))
                return Mock()
            with patch.object(preflight,'DIRECTORY',Path(folder)), patch('subprocess.run',side_effect=render_manifest), \
                    patch.dict(os.environ,BASE_SHA='base',HEAD_SHA='head'), patch('preflight.cached_qualification') as qualify:
                with self.assertRaises(PlanningError): preflight.build_quote(None)
            qualify.assert_not_called()

    def test_defaults_and_annotations_preserve_dependencies(self):
        for path, code in [
                ('a.py', 'def helper():\n    return 1\ndef f(helper=helper()):\n    return helper\n'),
                ('a.ts', 'function helper() { return 1; }\nfunction f(helper = helper()) { return helper; }\n')]:
            graph = Graph(sources({}, {path: code}))
            root = next(n for n in graph.module('new', path) if 'f' in graph.nodes[n].names)
            closure, _ = graph.closure([root])
            self.assertTrue(any('helper' in graph.nodes[n].names for n in closure), path)

    def test_multiple_and_relative_imports_include_all_modules(self):
        files = {'pkg/a.py': 'import first, second as s\nfrom . import helper\n',
                 'first.py': 'def first():\n    return 1\n', 'second.py': 'def second():\n    return 2\n',
                 'pkg/helper.py': 'def helper():\n    return 3\n'}
        graph = Graph(sources({}, files))
        closure, issues = graph.closure(graph.module('new', 'pkg/a.py'))
        self.assertFalse(issues)
        self.assertEqual({graph.nodes[n].path for n in closure}, set(files))

    def test_ambiguous_and_unsupported_imports_cannot_claim_completeness(self):
        examples = [
            {'a.ts': "import {f} from './helper';\n", 'helper.ts': 'export function f() {}', 'helper.js': 'export function f() {}'},
            {'a.py': 'from helper import absent\n', 'helper.py': 'def present(): pass\n'},
            {'a.rs': 'use crate::helper::*;\n', 'helper.rs': 'fn f() {}\nfn g() {}'},
            {'a.py': 'from helper import *\n', 'helper.py': 'def f(): pass\ndef g(): pass\n'}]
        for files in examples:
            graph = Graph(sources({}, files))
            self.assertTrue(graph.closure(graph.module('new', next(iter(files))))[1])

    def test_rust_crate_roots_and_external_library_boundary(self):
        files = {'src/a.rs': 'use crate::helper::allowed;\nuse std::path::Path;\nfn f() { allowed(); }\n',
                 'src/helper.rs': 'fn allowed() {}\n'}
        graph = Graph(sources({}, files))
        closure, issues = graph.closure(graph.module('new', 'src/a.rs'))
        self.assertFalse(issues)
        self.assertIn('src/helper.rs', {graph.nodes[n].path for n in closure})
        self.assertEqual(graph.external_imports, {'std::path::Path'})

    def test_multiline_python_imports_preserve_symbols_aliases_and_cycles(self):
        files = {'pkg/a.py': 'from .helper import (\n    allowed as ok, # comment\n    other,\n)\ndef submit(x):\n    return ok(x) and other(x)\n',
                 'pkg/helper.py': 'from .a import submit\ndef allowed(x):\n    return x > 0\ndef other(x):\n    return x < 10\ndef unused():\n    return 0\n'}
        graph = Graph(sources({}, files))
        closure, issues = graph.closure(graph.module('new', 'pkg/a.py'))
        self.assertFalse(issues)
        names = set().union(*(graph.nodes[n].names for n in closure))
        self.assertEqual(names, {'submit', 'allowed', 'other'})
        caller = next(n for n in graph.module('new', 'pkg/a.py') if 'submit' in graph.nodes[n].names)
        reached, issues = graph.closure([caller])
        self.assertFalse(issues)
        self.assertEqual(set(reached), set(closure))
        del files['pkg/helper.py']
        graph = Graph(sources({}, files))
        self.assertTrue(graph.closure(graph.module('new', 'pkg/a.py'))[1])

    def test_nested_rust_groups_aliases_and_module_self(self):
        files = {'src/a.rs': 'use crate::helper::{self, allowed as ok, nested::{one, two as second}};\nfn submit() { ok(); one(); second(); }\n',
                 'src/helper.rs': 'fn allowed() {}\n',
                 'src/helper/nested.rs': 'fn one() {}\nfn two() {}\nfn unrelated() {}\n'}
        graph = Graph(sources({}, files))
        closure, issues = graph.closure(graph.module('new', 'src/a.rs'))
        self.assertFalse(issues)
        names = set().union(*(graph.nodes[n].names for n in closure))
        self.assertEqual(names, {'submit', 'allowed', 'one', 'two'})

    def test_rust_self_and_super_group_paths(self):
        for statement in ['use self::helper::{allowed};', 'use super::nested::helper::{allowed as ok};']:
            files = {'src/nested/a.rs': statement, 'src/nested/helper.rs': 'fn allowed() {}'}
            graph = Graph(sources({}, files))
            closure, issues = graph.closure(graph.module('new', 'src/nested/a.rs'))
            self.assertFalse(issues)
            self.assertIn('src/nested/helper.rs', {graph.nodes[n].path for n in closure})

    def test_imported_objects_are_not_assumed_to_be_modules(self):
        for code in ['from external import client\ndef f():\n    return client.method()\n',
                     'def f(callback):\n    return callback()\n']:
            graph = Graph(sources({}, {'a.py': code}))
            closure, issues = graph.closure(graph.module('new', 'a.py'))
            self.assertFalse(issues)
            self.assertTrue(any(graph.nodes[n].limitations for n in closure))

    def test_git_snapshot_excludes_links_and_missing_source_blocks(self):
        def git(argv, **kwargs):
            if argv[1] in ('merge-base', 'rev-parse'):
                output = b'1234\n'
            else:
                self.assertEqual(argv[1:3], ['ls-tree', '-rz'])
                output = b'100644 blob abc\ta.py\x00120777 blob def\tlink.py\x00160000 commit 123\tsubmodule\x00'
            return Mock(stdout=output)
        with patch('subprocess.run', side_effect=git):
            snapshot = git_snapshots('base', 'head')
        self.assertEqual(snapshot['new'].files, {'a.py': 'abc'})
        with self.assertRaisesRegex(SourceError, 'source evidence unavailable'):
            pack(difference({}, {'a.py': 'x = 1\n'}), estimate_input_units, messages, sources({}, {}), 'baseline')

    def test_source_is_data_and_revision_changes_invalidate_plan(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder)/'executed'
            files = {'a.py': 'import os\nos.system('+repr('touch '+str(marker))+')\n'}
            diff, snapshot = difference({}, files), sources({}, files)
            frozen = pack(diff, estimate_input_units, messages, snapshot, 'baseline')['review_plan']
            self.assertFalse(marker.exists())
            changed = sources({}, {**files, 'unrelated.py': 'value = 2\n'})
            with self.assertRaisesRegex(PlanningError, 'plan changed'):
                restore(frozen, diff, changed)

    def test_execution_restores_exact_plan_without_repacking(self):
        from adversarial_review import review_diff
        files = {'a.py': 'def f():\n    return 1\n'}
        diff, snapshot = difference({}, files), sources({}, files)
        token = snapshots.set(snapshot)
        self.addCleanup(snapshots.reset, token)
        planned = review_diff(diff, list(files), strategy='baseline', dry_run=True)
        frozen = frozen_execution.set(planned['manifest']['review_plan'])
        self.addCleanup(frozen_execution.reset, frozen)
        with patch('adaptive_review.plan', side_effect=AssertionError('repacking forbidden')), \
                patch('adversarial_review.run_model_reviewer', return_value=('APPROVE', [], 'complete')) as assess:
            result = review_diff(diff, list(files), strategy='baseline')
        self.assertTrue(result['complete'])
        self.assertEqual(result['manifest'], planned['manifest'])
        self.assertEqual(assess.call_count, len(planned['manifest']['chunks']))

    def test_quote_mutation_rejected_before_source_loading(self):
        with patch('adversarial_review.extract_git_diff') as read:
            with self.assertRaisesRegex(PlanningError, 'quoted evidence plan changed'):
                admit({'baseline': {'chunks': [{'input_tokens': 1}]}}, {'plans': {'baseline': 'different'}})
        read.assert_not_called()

    def test_ci_rejects_context_gaps_before_qualification_or_reservations(self):
        script = Path(__file__).with_name('run_ci.py').resolve()
        with tempfile.TemporaryDirectory() as folder:
            previous = Path.cwd()
            self.addCleanup(os.chdir, previous)
            os.chdir(folder)
            directory = Path('review-result')
            directory.mkdir()
            binding = {'head': 'head', 'base': 'base', 'repository': 'org/repo', 'pr': 1, 'configuration': 'config'}
            (directory/'preflight.json').write_text(json.dumps({'blocked_reason': None, 'commit_id': 'head', 'quote': {'binding': binding}}))
            for strategy in ('baseline', 'adaptive'):
                (directory/(strategy+'-manifest.json')).write_text(json.dumps({
                    'files': 1, 'total_lines': 10, 'total_chunks': 1, 'total_units': 1, 'excluded_assets': [],
                    'test_evidence': {}, 'manifest': {'chunks': [{'omitted_support': ['missing dependency']}]}}))
            ledger = Mock()
            ledger.summary.return_value = {'committed_usd': 0}
            with patch.dict(os.environ, HEAD_SHA='head', BASE_SHA='base', GITHUB_REPOSITORY='org/repo', PR_NUMBER='1',
                            BEDROCK_REVIEW_ENABLED='true', REVIEW_COST_POLICY='true', GITHUB_STEP_SUMMARY=''), \
                    patch('preflight.restore', return_value=ledger), patch('quote.validate'), \
                    patch('quote.configuration', return_value='config'), patch('qualify.qualify') as qualify:
                runpy.run_path(str(script), run_name='__main__')
            qualify.assert_not_called()
            ledger.reserve.assert_not_called()
            report = json.loads((directory/'report.json').read_text())
            self.assertEqual(report['blocked_reason'], 'planning_failed')
            self.assertIn('missing dependency', '\n'.join(report['review_failures']))


if __name__ == '__main__':
    unittest.main()
