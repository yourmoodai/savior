"""Closure tests for platform-specific and frozen file dependencies."""
import unittest

from source_graph import Graph
from test_source_graph import sources


class PlatformEvidenceTests(unittest.TestCase):
    def graph(self, files, path, name):
        graph = Graph(sources({}, files))
        root = next(n for n in graph.module('new', path) if name in graph.nodes[n].names)
        nodes, issues = graph.closure([root])
        return [graph.nodes[n] for n in nodes], issues

    def test_platform_alias_keeps_both_nested_modules(self):
        files = {
            'src/lib.rs': 'mod transport;\nmod windows;\n',
            'src/windows.rs': 'pub fn unrelated() {}',
            'src/transport.rs': '#[cfg(unix)]\nmod unix;\n#[cfg(windows)]\nmod windows;\n'
                                '#[cfg(unix)]\nuse unix as platform;\n#[cfg(windows)]\nuse windows as platform;\n'
                                'pub fn serve() { platform::listen(); }',
            'src/transport/unix.rs': 'pub fn listen() { local(); }\nfn local() {}',
            'src/transport/windows.rs': 'pub fn listen() { pipe(); }\nfn pipe() {}',
        }
        nodes, issues = self.graph(files, 'src/transport.rs', 'serve')
        self.assertFalse(issues)
        text = '\n'.join(n.text for n in nodes)
        for item in ('fn local()', 'fn pipe()', '#[cfg(unix)]', '#[cfg(windows)]'):
            self.assertIn(item, text)
        self.assertNotIn('fn unrelated()', text)
        overlapping = dict(files)
        overlapping['src/transport.rs'] = files['src/transport.rs'].replace('#[cfg(windows)]', '#[cfg(unix)]')
        self.assertTrue(self.graph(overlapping, 'src/transport.rs', 'serve')[1])
        files['src/transport/windows.rs'] = 'pub fn absent() {}'
        self.assertTrue(self.graph(files, 'src/transport.rs', 'serve')[1])

    def test_grouped_self_retains_local_binding_and_accepts_registry_import(self):
        files = {'src/lib.rs': 'mod codec;\nmod consumer;',
                 'src/consumer.rs': 'use crate::codec::{self, Item};\nuse serde::de::{self, Error};\n'
                                    'fn check() -> Item { codec::read() }',
                 'src/codec.rs': 'pub struct Item;\npub fn read() -> Item { Item }'}
        graph = Graph(sources({}, files))
        nodes, issues = graph.closure(graph.module('new', 'src/consumer.rs'))
        self.assertFalse(issues)
        self.assertIn('serde::de', graph.external_imports)
        self.assertTrue(any('fn read()' in graph.nodes[n].text for n in nodes))

    def test_literal_javascript_import_retains_namespace_and_transitive_code(self):
        files = {'a.mjs': 'export async function load() { return import("./module.mjs"); }',
                 'module.mjs': 'import { helper } from "./helper.mjs";\n'
                               'export function first() { return helper(); }\nexport function second() {}',
                 'helper.mjs': 'export function helper() { return 71; }'}
        nodes, issues = self.graph(files, 'a.mjs', 'load')
        self.assertFalse(issues)
        text = '\n'.join(n.text for n in nodes)
        for fragment in ('function first()', 'function second()', 'return 71'):
            self.assertIn(fragment, text)
        for expression in ('import(path)', 'import("./absent.mjs")', 'import("./" + name)', 'import("/tmp/native.js")'):
            files['a.mjs'] = 'export function load(path, name) { return ' + expression + '; }'
            with self.subTest(expression=expression):
                self.assertTrue(self.graph(files, 'a.mjs', 'load')[1])

    def test_import_time_dynamic_namespace_retains_the_whole_module(self):
        files = {'main.mjs': 'import { use } from "./a.mjs";\nexport function run() { return use(); }',
                 'a.mjs': 'const startup = import("./loaded.js");\nexport function use() { return 1; }',
                 'loaded.js': 'export function opaque() { return eval("dynamic"); }'}
        self.assertTrue(self.graph(files, 'main.mjs', 'run')[1])

    def test_included_bytes_preserve_delimiters_and_block_missing_input(self):
        files = {'src/lib.rs': 'const RULES: &[u8] = include_bytes!("../rules.json");',
                 'rules.json': '[\n  "first",\n  "second"\n]\n'}
        nodes, issues = self.graph(files, 'src/lib.rs', 'RULES')
        self.assertFalse(issues)
        self.assertIn(files['rules.json'], [n.text for n in nodes])
        del files['rules.json']
        self.assertTrue(self.graph(files, 'src/lib.rs', 'RULES')[1])

    def test_hex_escape_in_a_format_string_retains_implicit_capture(self):
        files = {'src/lib.rs': 'const LABEL: &str = "test";\n'
                               r'fn render() -> String { format!("\x1b[31m{LABEL}\x1b[0m") }'}
        nodes, issues = self.graph(files, 'src/lib.rs', 'render')
        self.assertFalse(issues)
        self.assertTrue(any('LABEL' in n.names for n in nodes))

    def test_cargo_binary_binds_frozen_target_without_evaluating_env(self):
        manifest = '[package]\nname="example"\nversion="1.0.0"\n[[bin]]\nname="helper"\npath="src/main.rs"'
        files = {'Cargo.toml': manifest, 'src/main.rs': 'mod worker; fn main() { worker::run(); }',
                 'src/worker.rs': 'pub fn run() { println!("real target"); }',
                 'tests/run.rs': 'fn binary() { let _ = env!("CARGO_BIN_EXE_helper"); }'}
        nodes, issues = self.graph(files, 'tests/run.rs', 'binary')
        self.assertFalse(issues)
        self.assertEqual({n.path for n in nodes}, set(files))
        for path, value in [('Cargo.toml', manifest.replace('helper', 'other')), ('src/main.rs', 'fn main() { opaque!(); }')]:
            with self.subTest(path=path):
                self.assertTrue(self.graph({**files, path: value}, 'tests/run.rs', 'binary')[1])

    def test_parent_glob_preserves_registry_macro_provenance(self):
        files = {'Cargo.toml': '[package]\nname="app"\nversion="1.0.0"\n[dependencies]\nserde_json="1"',
                 'Cargo.lock': '[[package]]\nname="serde_json"\nversion="1.0.154"\n'
                               'source="registry+https://github.com/rust-lang/crates.io-index"',
                 'src/lib.rs': 'mod tests;\nuse serde_json::json;\npub struct Item;',
                 'src/tests.rs': 'use super::{Item, *};\nfn value() { let _ = json!({"key": 3}); }'}
        nodes, issues = self.graph(files, 'src/tests.rs', 'value')
        self.assertFalse(issues)
        self.assertIn('serde_json::json', {m['origin'] for n in nodes for m in n.macro_expansions})
        for path, addition in [('src/tests.rs', '\nuse other::*;'), ('Cargo.toml', '\n[patch.crates-io]\nserde_json={path="override"}')]:
            self.assertTrue(self.graph({**files, path: files[path] + addition}, 'src/tests.rs', 'value')[1])

    def test_select_tracks_branch_dependencies_and_rejects_opaque_expansions(self):
        files = {'Cargo.toml': '[package]\nname="app"\nversion="1.0.0"\n[dependencies]\ntokio="1.54.1"',
                 'Cargo.lock': '[[package]]\nname="tokio"\nversion="1.54.1"\n'
                               'source="registry+https://github.com/rust-lang/crates.io-index"',
                 'src/lib.rs': 'mod worker;\nasync fn run(s: Service) { tokio::select! {\n'
                               'value = worker::read() => s.save(value),\n_ = s.cancel() => (),\n} }',
                 'src/worker.rs': 'pub async fn read() -> u8 { 32 }'}
        nodes, issues = self.graph(files, 'src/lib.rs', 'run')
        self.assertFalse(issues)
        self.assertIn('src/worker.rs', [n.path for n in nodes])
        calls = {(c['expression'], c['line']) for n in nodes for c in n.limitations}
        self.assertIn(('s.save', 3), calls)
        self.assertIn(('s.cancel', 4), calls)
        for expr in ['worker::missing()', '{ hidden!() }', 's.cancel(), if enabled']:
            changed = files['src/lib.rs'].replace('worker::read()', expr)
            self.assertTrue(self.graph({**files, 'src/lib.rs': changed}, 'src/lib.rs', 'run')[1])

    def test_win32_globs_require_a_verified_namespace_and_locked_archive(self):
        files = {'Cargo.toml': '[package]\nname="app"\nversion="1.0.0"\n[dependencies]\nwindows-sys="0.61.2"',
                 'Cargo.lock': '[[package]]\nname="windows-sys"\nversion="0.61.2"\n'
                               'source="registry+https://github.com/rust-lang/crates.io-index"\n'
                               'checksum="ae137229bcbd6cdf0f7b80a31df61766145077ddf49416a728b02cb3921ff3fc"',
                 'src/lib.rs': 'use windows_sys::Win32::{Foundation::*, System::Pipes::*};\n'
                               'fn value() { let _ = format!("test"); }'}
        self.assertFalse(self.graph(files, 'src/lib.rs', 'value')[1])
        controls = [('Cargo.lock', files['Cargo.lock'].replace('ae137', 'ba137')),
                    ('Cargo.toml', files['Cargo.toml'] + '\n[patch.crates-io]\nwindows-sys={path="custom"}'),
                    ('src/lib.rs', files['src/lib.rs'].replace('Pipes', 'Unverified')),
                    ('src/lib.rs', 'mod windows_sys {}\n' + files['src/lib.rs']),
                    ('src/lib.rs', 'use local::windows_sys;\n' + files['src/lib.rs'])]
        for path, value in controls:
            with self.subTest(value=value):
                self.assertTrue(self.graph({**files, path: value}, 'src/lib.rs', 'value')[1])
