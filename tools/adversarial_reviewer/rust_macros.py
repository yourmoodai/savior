"""Bounded dependency expansion for known external expression macros."""
import json
import hashlib
import posixpath
import re
import tomllib

from rust_evidence import use_bindings, use_paths


STANDARD = {'assert', 'assert_eq', 'assert_ne', 'debug_assert', 'debug_assert_eq',
            'debug_assert_ne', 'format', 'format_args', 'println', 'eprintln',
            'print', 'eprint', 'write', 'writeln', 'vec', 'matches', 'panic',
            'unreachable', 'todo', 'unimplemented', 'cfg', 'concat', 'env', 'include_str', 'include_bytes'}
FORMATTED = STANDARD - {'vec', 'matches', 'cfg', 'concat', 'env', 'include_str', 'include_bytes'}
LIBRARIES = {'serde_json': {'json'}, 'rusqlite': {'params'}, 'duckdb': {'params'}, 'tokio': {'select'}}
VERSIONS = {'serde_json': {'1.0.154'}, 'rusqlite': {'0.38.0', '0.43.0'},
            'duckdb': {'1.10506.1', '1.20001.0'}, 'tokio': {'1.54.1'}}


# These generated Win32 modules export ABI functions, types and constants. An
# unrecognized namespace or crate revision could export a macro of the same name.
WIN32_NAMESPACES = {('Foundation',), ('Security',), ('Security', 'Authorization'),
                    ('Storage', 'FileSystem'), ('System', 'Threading'), ('System', 'Pipes'), ('System', 'IO')}
WIN32_ARCHIVES = {'0.52.0': '282be5f36a8ce781fad8c8ae18fa3f9beff57ec1b52cb3de0789201425d9a33d',
                  '0.61.2': 'ae137229bcbd6cdf0f7b80a31df61766145077ddf49416a728b02cb3921ff3fc'}


def literal(text):
    raw = re.fullmatch(r'r(?P<hash>#{0,255})"(?P<body>.*)"(?P=hash)', text, re.S)
    if raw:
        return raw['body']
    # Rust permits ASCII hex escapes in cooked strings. Preserve escaped backslashes.
    def escape(match):
        token = match.group()
        return '\\u00' + token[2:] if token.startswith('\\x') else token
    return json.loads(re.sub(r'\\(?:x[0-7][0-9a-fA-F]|.)', escape, text))


def format_captures(text):
    captures, cursor = set(), 0
    while cursor < len(text):
        if text[cursor:cursor+2] in ('{{', '}}'):
            cursor += 2
        elif text[cursor] == '{':
            end = text.find('}', cursor+1)
            if end < 0:
                raise ValueError('incomplete format field')
            field, _, specification = text[cursor+1:end].partition(':')
            if field.isidentifier():
                captures.add(field)
            captures.update(re.findall(r'\b(\w+)\$', specification))
            cursor = end+1
        else:
            cursor += 1
    return captures


def invocations(node):
    if node.type == 'macro_invocation':
        yield node.child_by_field_name('macro').text.decode(), next(n for n in node.named_children if n.type == 'token_tree'), node
        return
    if node.type == 'token_tree':
        children = node.children
        for index, child in enumerate(children):
            if child.type == '!' and index and index + 1 < len(children) and children[index + 1].type == 'token_tree':
                begin = index - 1
                while begin >= 2 and children[begin - 1].type == '::':
                    begin -= 2
                name = ''.join(n.text.decode() for n in children[begin:index])
                yield name, children[index + 1], children[begin]
        return
    for child in node.named_children:
        yield from invocations(child)


def select_expressions(arguments, parse):
    """Parse simple binding = future => handler branches as ordinary Rust expressions."""
    branch = []
    count = 0
    for token in [*arguments.children[1:-1], None]:
        if token is not None and token.type != ',':
            branch.append(token)
            continue
        if not branch:
            if token is not None or not count:
                raise ValueError('empty select branch')
            break
        count += 1
        arrows = [i for i, t in enumerate(branch) if t.type == '=>']
        if (len(arrows) != 1 or len(branch) < 5 or branch[0].type not in ('identifier', '_')
                or branch[1].type != '=' or not 2 < arrows[0] < len(branch)-1):
            raise ValueError('unsupported select branch')
        for expr in (branch[2:arrows[0]], branch[arrows[0]+1:]):
            begin = expr[0].start_byte - arguments.start_byte
            end = expr[-1].end_byte - arguments.start_byte
            source = b'fn __review() { let _ = ' + arguments.text[begin:end] + b'; }'
            root = parse('rust').parse(source).root_node
            if root.has_error:
                raise ValueError('unsupported select expression')
            value = root.named_children[0].child_by_field_name('body').named_children[0].child_by_field_name('value')
            if value is None or value.end_byte != len(source)-3:
                raise ValueError('incomplete select expression')
            yield value, expr[0].start_point.row
        branch = []


def select_dependencies(arguments, parse):
    paths, calls = set(), []
    for expression, offset in select_expressions(arguments, parse):
        pending = [expression]
        while pending:
            node = pending.pop()
            if node.type.endswith('_item') or node.type in ('use_declaration', 'macro_definition', 'macro_invocation'):
                raise ValueError('unsupported declaration or macro in select')
            if node.type in ('scoped_identifier', 'scoped_type_identifier'):
                path = node.text.decode()
                if not re.fullmatch(r'(?:[a-zA-Z_]\w*::)+[a-zA-Z_]\w*', path):
                    raise ValueError('unsupported qualified select expression')
                paths.add(path)
            if node.type == 'call_expression':
                function = node.child_by_field_name('function')
                if function.type in ('field_expression', 'identifier'):
                    calls.append({'line': offset + function.start_point.row + 1, 'expression': function.text.decode(),
                                  'receiver': function.type == 'field_expression'})
                elif function.type not in ('scoped_identifier',):
                    raise ValueError('unsupported indirect call in select')
            pending.extend(node.named_children)
    return sorted(paths), calls


class MacroDependencies:
    def __init__(self, rust):
        self.rust, self.shadow_cache = rust, {}
        self.win32_providers = {}

    def registry_provider(self, path, library):
        directory, manifest = self.rust.manifest(path)
        specifications = [table[section][library]
                          for table in [manifest, *manifest.get('target', {}).values()]
                          for section in ['dependencies', 'dev-dependencies', 'build-dependencies']
                          if library in table.get(section, {})]
        if not specifications:
            return False
        ancestors = [directory]
        while ancestors[-1]:
            ancestors.append(posixpath.dirname(ancestors[-1]))
        workspaces = [tomllib.loads(source) for parent in ancestors
                      if (source := self.rust.snapshot.read(posixpath.join(parent, 'Cargo.toml'))) is not None]
        for specification in specifications:
            if isinstance(specification, dict) and specification.get('workspace'):
                specification = next((m['workspace']['dependencies'][library] for m in workspaces
                                      if library in m.get('workspace', {}).get('dependencies', {})), None)
            if specification is None:
                return False
            if isinstance(specification, dict) and (specification.get('package', library) != library
                    or any(key in specification for key in ('git', 'path', 'registry'))):
                return False
        for manifest in workspaces:
            if manifest.get('replace'):
                return False
            for patches in manifest.get('patch', {}).values():
                if any(name == library or value.get('package') == library for name, value in patches.items()):
                    return False
        for parent in ancestors:
            for name in ['.cargo/config.toml', '.cargo/config']:
                source = self.rust.snapshot.read(posixpath.join(parent, name))
                if source is not None:
                    config = tomllib.loads(source)
                    if config.get('source') or config.get('paths'):
                        return False
        return True

    def win32_provider(self, path):
        directory, manifest = self.rust.manifest(path)
        if directory not in self.win32_providers:
            lock = self.rust.snapshot.read('Cargo.lock')
            packages = [p for p in tomllib.loads(lock).get('package', []) if p['name'] == 'windows-sys'] if lock else []
            alternate = any('windows_sys' in table.get(section, {})
                            for table in [manifest, *manifest.get('target', {}).values()]
                            for section in ('dependencies', 'dev-dependencies', 'build-dependencies'))
            verified = (not alternate and bool(packages) and self.registry_provider(path, 'windows-sys')
                and all(p.get('source') == 'registry+https://github.com/rust-lang/crates.io-index'
                        and p['version'] in WIN32_ARCHIVES and p.get('checksum') == WIN32_ARCHIVES[p['version']]
                        for p in packages))
            self.win32_providers[directory] = {p['version']: p['checksum'] for p in packages} if verified else {}
        return self.win32_providers[directory]

    def win32_globs(self, path, argument, shadowed):
        if 'windows_sys' in shadowed:
            return False
        imports = [parts for parts, _ in use_paths(argument)]
        return (bool(imports) and all(parts[:2] == ('windows_sys', 'Win32') and
                (parts[-1] != '*' or parts[2:-1] in WIN32_NAMESPACES) for parts in imports)
                and self.win32_provider(path))

    def origin(self, path, name, location=None, providers=None):
        leaf = name.split('::')[-1]
        directory, _ = self.rust.manifest(path)
        if directory not in self.shadow_cache:
            shadowed, wildcard = set(), False
            for file in self.rust.snapshot.files:
                if not file.endswith('.rs') or (directory and not file.startswith(directory + '/')):
                    continue
                source = self.rust.snapshot.read(file)
                shadowed.update(re.findall(r'\bmacro_rules\s*!\s*(\w+)', source))
                wildcard |= bool(re.search(r'#\s*\[\s*macro_use\b', source))
                shadowed.update(re.findall(r'\bmod\s+(std|core|alloc|serde_json|rusqlite|duckdb|tokio|windows_sys)\b', source))
                shadowed.update(re.findall(r'\bas\s+(std|core|alloc|serde_json|rusqlite|duckdb|tokio|windows_sys)\b', source))
                names = {'windows_sys', 'tokio'}
                if any(name in source for name in names):
                    pending = [self.rust.tree(file)]
                    while pending:
                        node = pending.pop()
                        if node.type == 'use_declaration':
                            shadowed.update(names & use_bindings(node.child_by_field_name('argument')))
                        elif node.type == 'extern_crate_declaration':
                            shadowed.update(names & set(re.findall(r'\w+', node.text.decode())))
                        pending.extend(node.named_children)
                for alias, origins in self.rust.aliases(file).items():
                    if alias in STANDARD and any(origin not in {(library, alias) for library in ('std', 'core', 'alloc')}
                                                 for origin in origins):
                        shadowed.add(alias)
            self.shadow_cache[directory] = shadowed, wildcard
        shadowed, wildcard = self.shadow_cache[directory]
        if leaf in shadowed or wildcard:
            return None
        tree = self.rust.tree(path)
        pending = list(tree.named_children) if tree is not None else []
        scoped_aliases = set()
        inherited_globs = []
        while pending:
            node = pending.pop()
            if node.type == 'use_declaration':
                if location is not None and not (node.parent.start_byte <= location.start_byte < node.parent.end_byte):
                    continue
                argument = node.child_by_field_name('argument')
                text = argument.text.decode()
                if re.search(r'\bas\s+(std|core|alloc|serde_json|rusqlite|duckdb|tokio)\b', text):
                    return None
                if '*' in text and not text.startswith(('self::', 'super::', 'crate::', 'std::', 'core::', 'alloc::')):
                    archives = self.win32_globs(path, argument, shadowed)
                    if not archives:
                        return None
                    if providers is not None:
                        providers['windows-sys'] = archives
                if '*' in text:
                    inherited_globs.extend(node.parent == tree and parts == ('super', '*')
                                           for parts, _ in use_paths(argument) if parts[-1] == '*')
                if '::' not in name and node.parent != tree and re.search(r'\b'+re.escape(leaf)+r'\b', text):
                    if not re.fullmatch(r'(std|core|alloc|serde_json|rusqlite|duckdb|tokio)::'+re.escape(leaf), text):
                        return None
                    scoped_aliases.add(tuple(text.split('::')))
            pending.extend(node.named_children)
        aliases = scoped_aliases or self.rust.aliases(path).get(leaf, set())
        if ('::' not in name and not aliases and leaf not in STANDARD and inherited_globs == [True]):
            parents = {self.rust.routes[root, path][-1] for root in self.rust.roots(path)
                       if self.rust.routes[root, path]}
            if len(parents) == 1:
                parent = parents.pop()
                aliases = self.rust.aliases(parent).get(leaf, set())
                if not aliases or not self.origin(parent, leaf, providers=providers):
                    return None
        if '::' in name:
            candidates = {tuple(name.split('::'))}
        elif aliases:
            candidates = aliases
        else:
            candidates = {('std', leaf)} if leaf in STANDARD else set()
        if len(candidates) != 1:
            return None
        candidate = next(iter(candidates))
        library, macro = candidate[0], candidate[-1]
        if library in shadowed or len(candidate) != 2:
            return None
        if len(candidate) == 2 and library in ('std', 'core', 'alloc') and macro in STANDARD:
            _, manifest = self.rust.manifest(path)
            if any(library in table.get(section, {}) for table in [manifest, *manifest.get('target', {}).values()]
                   for section in ('dependencies', 'dev-dependencies', 'build-dependencies')):
                return None
            if '::' not in name and not aliases and '#![no_implicit_prelude]' in self.rust.snapshot.read(path):
                return None
            return library + '::' + macro
        if macro not in LIBRARIES.get(library, set()) or not self.registry_provider(path, library):
            return None
        lock = self.rust.snapshot.read('Cargo.lock')
        if lock is None:
            return None
        packages = tomllib.loads(lock).get('package', [])
        providers = [p for p in packages if p['name'] == library]
        if not providers or not all(p.get('source') == 'registry+https://github.com/rust-lang/crates.io-index'
                                    and p['version'] in VERSIONS[library] for p in providers):
            return None
        return library + '::' + macro

    def cargo_value(self, path, name):
        directory, manifest = self.rust.manifest(path)
        if name == 'CARGO_MANIFEST_DIR':
            return directory or '.'
        if name != 'CARGO_PKG_VERSION':
            raise ValueError('unsupported build environment')
        value = manifest.get('package', {}).get('version')
        if isinstance(value, dict) and value.get('workspace'):
            while directory:
                directory = posixpath.dirname(directory)
                source = self.rust.snapshot.read(posixpath.join(directory, 'Cargo.toml'))
                if source is not None:
                    value = tomllib.loads(source).get('workspace', {}).get('package', {}).get('version')
                    if value is not None:
                        break
        if not isinstance(value, str):
            raise ValueError('unresolved package version')
        return value

    def binary_source(self, path, name):
        directory, manifest = self.rust.manifest(path)
        build = manifest.get('package', {}).get('build')
        if build not in (None, False) or (build is None and posixpath.join(directory, 'build.rs') in self.rust.snapshot.files):
            raise ValueError('custom build script may affect the test binary')
        relative = posixpath.relpath(path, directory or '.')
        if not (re.fullmatch(r'tests/[^/]+\.rs', relative) and manifest.get('package', {}).get('autotests', True)):
            raise ValueError('binary path is not bound to a Cargo integration test')
        targets = [entry for entry in manifest.get('bin', []) if entry.get('name') == name]
        if len(targets) != 1 or not isinstance(targets[0].get('path'), str):
            raise ValueError('missing or ambiguous Cargo binary target')
        source = targets[0]['path']
        if not re.fullmatch(r'(?:[\w-]+/)*[\w-]+\.rs', source):
            raise ValueError('unsupported Cargo binary source')
        source = posixpath.join(directory, source)
        if source not in self.rust.snapshot.files:
            raise ValueError('missing Cargo binary source')
        return posixpath.join(directory, 'Cargo.toml'), source

    def constant(self, path, name, arguments):
        values = []
        pending = list(arguments.named_children)
        while pending:
            node = pending.pop(0)
            if node.type in ('string_literal', 'raw_string_literal'):
                values.append(literal(node.text.decode()))
            elif node.type == 'identifier' and len(pending) >= 1:
                nested = pending.pop(0)
                if nested.type != 'token_tree' or node.text.decode() not in ('concat', 'env'):
                    raise ValueError('unsupported constant expression')
                values.append(self.constant(path, node.text.decode(), nested))
            else:
                raise ValueError('unsupported constant expression')
        if name == 'env':
            if len(values) != 1:
                raise ValueError('unsupported environment expression')
            return self.cargo_value(path, values[0])
        return ''.join(values)

    def expand(self, path, call, side):
        expansions, refs, files = [], set(), set()
        pending = list(invocations(call))
        while pending:
            name, arguments, location = pending.pop()
            providers = {}
            origin = self.origin(path, name, location, providers)
            if origin is None:
                return [], set(), set(), False
            macro = origin.split('::')[-1]
            record = {'path': path, 'side': side, 'line': location.start_point.row + 1,
                      'origin': origin, 'arguments_sha256': hashlib.sha256(arguments.text).hexdigest(),
                      'contract': 'external macro; all explicit argument identifiers retain their source dependencies'}
            if providers:
                record['glob_provider_archives'] = providers
            if origin == 'tokio::select':
                record['explicit_paths'], record['calls'] = select_dependencies(arguments, self.rust.parse)
            if macro in FORMATTED:
                for node in arguments.named_children:
                    if node.type in ('string_literal', 'raw_string_literal') and not node.text.startswith(b'b'):
                        refs.update(format_captures(literal(node.text.decode())))
                record['implicit_captures'] = sorted(refs)
            binary = (re.fullmatch(rb'\(\s*"CARGO_BIN_EXE_([a-zA-Z0-9_-]+)"\s*\)', arguments.text)
                      if macro == 'env' else None)
            if binary:
                manifest, source = self.binary_source(path, binary[1].decode())
                files.add(manifest)
                record['binary_source'] = source
                record['contract'] = 'Cargo integration-test binary; runtime path is assigned by the build'
            elif macro in ('concat', 'env'):
                record['constant'] = self.constant(path, macro, arguments)
            elif macro in ('include_str', 'include_bytes'):
                value = self.constant(path, 'concat', arguments)
                if 'CARGO_MANIFEST_DIR' in arguments.text.decode():
                    target = posixpath.normpath(value)
                else:
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(path), value))
                if target.startswith('../') or target.startswith('/') or target not in self.rust.snapshot.files:
                    return [], set(), set(), False
                files.add(target)
                record['source_file'] = target
            expansions.append(record)
            pending.extend(invocations(arguments))
        return expansions, refs, files, True
