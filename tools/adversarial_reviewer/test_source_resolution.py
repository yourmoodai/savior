import json
import unittest

from bedrock_client import estimate_input_units
from chunk_plan import manifest
from review_plan import pack, require_feasible, restore
from source_graph import Graph
from test_source_graph import difference, messages, sources


class SourceResolutionTests(unittest.TestCase):
    def test_javascript_import_keeps_initializers_and_called_bodies(self):
        files = {'a.mjs': 'import { used } from "./helper.mjs";\nexport function f() { return used(); }\n',
                 'helper.mjs': 'function setup() { return 23; }\nconst STATE = setup();\nexport function used(x=setup()) { return STATE; }\nexport function unused() { return eval("missing"); }\n'}
        graph = Graph(sources({}, files))
        root = next(n for n in graph.module('new', 'a.mjs') if 'f' in graph.nodes[n].names)
        closure, issues = graph.closure([root])
        text = '\n'.join(graph.nodes[n].text for n in closure)
        self.assertFalse(issues)
        for expected in ['return 23', 'const STATE = setup()', 'return STATE']:
            self.assertIn(expected, text)
        self.assertNotIn('eval("missing")', text)

    def test_javascript_callbacks_and_default_reflection_stay_in_closure(self):
        for code in ['register(() => eval("missing"));\nexport function used() {}\n',
                     'export function unused(x=eval("missing")) {}\nexport function used() {}\n']:
            files = {'a.mjs': 'import { used } from "./helper.mjs";\nused();\n', 'helper.mjs': code}
            graph = Graph(sources({}, files))
            self.assertTrue(graph.closure(graph.module('new', 'a.mjs'))[1])

    def test_javascript_class_constructor_fields_and_this_calls_are_retained(self):
        files = {'a.mjs': 'class C {\n static state = setup();\n constructor() { initialize(); }\n helper() { return 42; }\n called() { return this.helper(); }\n unused() { return eval("missing"); }\n}\nfunction setup() { return 1; }\nfunction initialize() { return 2; }\n'}
        graph = Graph(sources({}, files))
        root = next(n for n in graph.module('new', 'a.mjs') if 'C.called' in graph.nodes[n].names)
        closure, issues = graph.closure([root])
        text = '\n'.join(graph.nodes[n].text for n in closure)
        self.assertFalse(issues)
        for expected in ['class C', 'static state = setup()', 'return 1', 'initialize()', 'return 2', 'return 42']:
            self.assertIn(expected, text)
        self.assertNotIn('eval("missing")', text)

    def test_object_eval_is_runtime_dispatch_but_builtin_eval_blocks(self):
        for code, blocked in [('def f(model):\n    return model.eval()\n', False),
                              ('def f(source):\n    return eval(source)\n', True),
                              ('import builtins as core\ndef f(source):\n    return core.eval(source)\n', True)]:
            graph = Graph(sources({}, {'a.py': code}))
            closure, issues = graph.closure(graph.module('new', 'a.py'))
            self.assertEqual(bool(issues), blocked)
            if not blocked:
                self.assertTrue(any(graph.nodes[n].limitations for n in closure))

    def test_known_macro_captures_retain_dependencies_and_frozen_evidence(self):
        files = {'a.rs': 'const LABEL: &str = "local";\nfn helper() -> u8 { 1 }\nfn render() -> String { format!("{LABEL} {}", helper()) }\n'}
        snapshot, diff = sources({}, files), difference({}, files)
        planned = pack(diff, estimate_input_units, messages, snapshot, 'adaptive')
        require_feasible({'adaptive': manifest(planned)})
        self.assertTrue(any(c['macro_expansions'] for c in planned['chunks']))
        self.assertIn('LABEL', str(planned['chunks'][0]['macro_expansions']))
        restore(planned['review_plan'], diff, snapshot)

    def test_unknown_nested_and_shadowed_macros_stay_blocked(self):
        for code in ['fn f() { vec![arbitrary!()]; }',
                     'macro_rules! format { ($x:expr) => { hidden() } }\nfn f() { format!("x"); }',
                     'macro_rules ! format { ($x:expr) => { hidden() } }\nfn f() { format!("x"); }',
                     'use custom as std;\nfn f() { std::format!("x"); }',
                     'fn f() { use custom::format; format!("x"); }',
                     'use custom::*;\nfn f() { format!("x"); }',
                     'fn f() { std::custom::format!("x"); }']:
            graph = Graph(sources({}, {'a.rs': code}))
            self.assertTrue(graph.closure(graph.module('new', 'a.rs'))[1])

    def test_rust_import_does_not_pull_unrelated_nested_test_macros(self):
        files = {'src/lib.rs': 'mod model;\npub struct Error;\n#[cfg(test)]\nmod tests { proptest! { arbitrary_generated_items } }\n',
                 'src/model.rs': 'use crate::Error;\npub fn check() -> Error { Error }\n'}
        graph = Graph(sources({}, files))
        closure, issues = graph.closure(graph.module('new', 'src/model.rs'))
        self.assertFalse(issues)
        self.assertNotIn('arbitrary_generated_items', '\n'.join(graph.nodes[n].text for n in closure))

    def test_hyphenated_workspace_path_dependency_retains_local_source(self):
        files = {'Cargo.toml': '[workspace]\nmembers=["a", "b"]\n[workspace.dependencies]\nlocal-core={path="b"}\n',
                 'a/Cargo.toml': '[package]\nname="a"\nversion="1.0.0"\n[dependencies]\nlocal-core.workspace=true\n',
                 'a/src/lib.rs': 'use local_core::Policy;\npub fn policy() -> Policy { Policy::Local }\n',
                 'b/Cargo.toml': '[package]\nname="local-core"\nversion="1.0.0"\n',
                 'b/src/lib.rs': 'pub enum Policy { Local }\n'}
        graph = Graph(sources({}, files))
        closure, issues = graph.closure(graph.module('new', 'a/src/lib.rs'))
        self.assertFalse(issues)
        self.assertIn('b/src/lib.rs', {graph.nodes[n].path for n in closure})

    def test_uppercase_rust_module_is_resolved_as_a_module(self):
        files = {'src/lib.rs': 'mod Upper;\npub fn run() -> u8 { Upper::value() }\n',
                 'src/Upper.rs': 'pub fn value() -> u8 { 42 }\n'}
        graph = Graph(sources({}, files))
        root = next(n for n in graph.module('new', 'src/lib.rs') if 'run' in graph.nodes[n].names)
        closure, issues = graph.closure([root])
        self.assertFalse(issues)
        self.assertIn('src/Upper.rs', {graph.nodes[n].path for n in closure})

    def test_standard_assert_accepts_bytes_without_treating_them_as_format_strings(self):
        graph = Graph(sources({}, {'a.rs': 'fn check() { assert_eq!(b"value", b"value"); }'}))
        self.assertFalse(graph.closure(graph.module('new', 'a.rs'))[1])

    def test_format_captures_survive_adjacent_escaped_braces(self):
        files = {'a.rs': 'const LABEL: &str = "label";\nfn render() -> String { format!("{{{LABEL}}}") }\n'}
        graph = Graph(sources({}, files))
        root = next(n for n in graph.module('new', 'a.rs') if 'render' in graph.nodes[n].names)
        closure, issues = graph.closure([root])
        self.assertFalse(issues)
        self.assertTrue(any('LABEL' in graph.nodes[n].names for n in closure))

    def test_registry_macro_contract_rejects_local_replacement(self):
        files = {'Cargo.toml': '[package]\nname="fixture"\nversion="1.0.0"\n[dependencies]\nserde_json="1.0"\n',
                 'Cargo.lock': '[[package]]\nname="serde_json"\nversion="1.0.154"\nsource="registry+https://github.com/rust-lang/crates.io-index"\n',
                 'src/lib.rs': 'pub fn value() { let _ = serde_json::json!({"value": 1}); }\n'}
        graph = Graph(sources({}, files))
        self.assertFalse(graph.closure(graph.module('new', 'src/lib.rs'))[1])
        files['Cargo.toml'] += '[patch.crates-io]\nserde_json={path="local-json"}\n'
        graph = Graph(sources({}, files))
        self.assertTrue(graph.closure(graph.module('new', 'src/lib.rs'))[1])

    def test_nested_glob_does_not_shadow_macro_in_another_scope(self):
        files = {'src/lib.rs': 'pub fn check() { debug_assert_eq!(1, 1); }\nmod tests { use arbitrary::prelude::*; fn check() { format!("x"); } }\n'}
        graph = Graph(sources({}, files))
        nodes = graph.module('new', 'src/lib.rs')
        production = next(n for n in nodes if graph.nodes[n].names == {'check'})
        nested = next(n for n in nodes if graph.nodes[n].names == {'tests.check'})
        self.assertFalse(graph.closure([production])[1])
        self.assertTrue(graph.closure([nested])[1])

    def test_qualified_macro_is_not_shadowed_by_an_unrelated_function_import(self):
        files = {'Cargo.toml': '[package]\nname="fixture"\nversion="1.0.0"\n[dependencies]\nserde_json="1.0"\n',
                 'Cargo.lock': '[[package]]\nname="serde_json"\nversion="1.0.154"\nsource="registry+https://github.com/rust-lang/crates.io-index"\n',
                 'src/lib.rs': 'pub fn value() { let _ = serde_json::json!({"value": 1}); }\nfn other() { use serde_json::json; let _ = json!({"other": 2}); }\n'}
        graph = Graph(sources({}, files))
        self.assertFalse(graph.closure(graph.module('new', 'src/lib.rs'))[1])

    def test_missing_rust_module_stays_blocked(self):
        graph = Graph(sources({}, {'src/lib.rs': 'mod absent;\n'}))
        self.assertTrue(graph.closure(graph.module('new', 'src/lib.rs'))[1])

    def test_literal_includes_retain_exact_source_and_missing_files_block(self):
        for present in [True, False]:
            files = {'src/lib.rs': 'const RULE: &str = include_str!("../rule.txt");\n'}
            if present:
                files['rule.txt'] = 'complete rule text\n'
            graph = Graph(sources({}, files))
            closure, issues = graph.closure(graph.module('new', 'src/lib.rs'))
            self.assertEqual(bool(issues), not present)
            if present:
                self.assertIn('rule.txt', {graph.nodes[n].path for n in closure})

    def test_python_submodule_imports_share_one_package_binding(self):
        files = {'a.py': 'import urllib.request\nimport urllib.error\ndef request():\n    return urllib.request.Request("https://example.invalid")\n'}
        graph = Graph(sources({}, files))
        closure, issues = graph.closure(graph.module('new', 'a.py'))
        self.assertFalse(issues)
        self.assertEqual({graph.nodes[n].path for n in closure}, {'a.py'})
        self.assertEqual(graph.external_imports, {'urllib/request', 'urllib/error'})

    def test_shadowing_package_import_remains_ambiguous(self):
        files = {'a.py': 'import urllib.request\nfrom replacement import client as urllib\ndef request():\n    return urllib.request.Request("https://example.invalid")\n'}
        graph = Graph(sources({}, files))
        self.assertTrue(graph.closure(graph.module('new', 'a.py'))[1])

    def test_rust_crate_root_symbols_and_reexports(self):
        files = {
            'crates/core/src/lib.rs': 'mod artifact;\nmod model;\npub use artifact::Policy;\npub enum Error { InvalidInput }\npub type Result<T> = std::result::Result<T, Error>;\n',
            'crates/core/src/artifact.rs': 'pub enum Policy { Local }\n',
            'crates/core/src/model.rs': 'use crate::{Error, Result, Policy};\npub fn check() -> Result<Policy> { Err(Error::InvalidInput) }\n',
        }
        graph = Graph(sources({}, files))
        closure, issues = graph.closure(graph.module('new', 'crates/core/src/model.rs'))
        self.assertFalse(issues)
        self.assertEqual({graph.nodes[n].path for n in closure}, set(files))

    def test_missing_rust_root_symbol_still_blocks(self):
        files = {'src/lib.rs': 'pub struct Present;\n', 'src/a.rs': 'use crate::Absent;\n'}
        graph = Graph(sources({}, files))
        self.assertTrue(graph.closure(graph.module('new', 'src/a.rs'))[1])

    def test_npm_lock_entries_fit_without_omitting_changed_evidence(self):
        packages = {'': {'name': 'fixture', 'dependencies': {'dep0': '^1.0.0'}}}
        packages.update({f'node_modules/dep{i}': {'version': '1.0.0', 'integrity': 'sha512-' + 'a' * 400}
                         for i in range(300)})
        old = {'package-lock.json': json.dumps({'name': 'fixture', 'lockfileVersion': 3,
                                               'packages': packages}, indent=2) + '\n'}
        packages['node_modules/dep0']['version'] = '1.0.1'
        new = {'package-lock.json': json.dumps({'name': 'fixture', 'lockfileVersion': 3,
                                               'packages': packages}, indent=2) + '\n'}
        snapshot, diff = sources(old, new), difference(old, new)
        for strategy in ['baseline', 'adaptive']:
            with self.subTest(strategy=strategy):
                planned = pack(diff, estimate_input_units, messages, snapshot, strategy)
                require_feasible({strategy: manifest(planned)})
                restore(planned['review_plan'], diff, snapshot)
                support = '\n'.join(c['support'] for c in planned['chunks'])
                self.assertIn('"lockfileVersion": 3', support)
                self.assertIn('"node_modules/dep0"', support)
                self.assertIn('"version": "1.0.0"', support)
                self.assertIn('"version": "1.0.1"', support)
                self.assertNotIn('"node_modules/dep200"', support)


if __name__ == '__main__':
    unittest.main()
