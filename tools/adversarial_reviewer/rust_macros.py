"""Bounded dependency expansion for known external expression macros."""
import json
import hashlib
import posixpath
import re
import tomllib


STANDARD = {'assert', 'assert_eq', 'assert_ne', 'debug_assert', 'debug_assert_eq',
            'debug_assert_ne', 'format', 'format_args', 'println', 'eprintln',
            'print', 'eprint', 'write', 'writeln', 'vec', 'matches', 'panic',
            'unreachable', 'todo', 'unimplemented', 'cfg', 'concat', 'env', 'include_str'}
FORMATTED = STANDARD - {'vec', 'matches', 'cfg', 'concat', 'env', 'include_str'}
LIBRARIES = {'serde_json': {'json'}, 'rusqlite': {'params'}, 'duckdb': {'params'}}
VERSIONS = {'serde_json': {'1.0.154'}, 'rusqlite': {'0.38.0', '0.43.0'},
            'duckdb': {'1.10506.1', '1.20001.0'}}


def literal(text):
    raw = re.fullmatch(r'r(?P<hash>#{0,255})"(?P<body>.*)"(?P=hash)', text, re.S)
    if raw:
        return raw['body']
    return json.loads(text)


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


class MacroDependencies:
    def __init__(self, rust):
        self.rust, self.shadow_cache = rust, {}

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
            for patches in manifest.get('patch', {}).values():
                if any(name == library or value.get('package') == library for name, value in patches.items()):
                    return False
        for parent in ancestors:
            for name in ['.cargo/config.toml', '.cargo/config']:
                source = self.rust.snapshot.read(posixpath.join(parent, name))
                if source is not None and tomllib.loads(source).get('source'):
                    return False
        return True

    def origin(self, path, name):
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
                shadowed.update(re.findall(r'\bmod\s+(std|core|alloc|serde_json|rusqlite|duckdb)\b', source))
                shadowed.update(re.findall(r'\bas\s+(std|core|alloc|serde_json|rusqlite|duckdb)\b', source))
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
        while pending:
            node = pending.pop()
            if node.type == 'use_declaration':
                argument = node.child_by_field_name('argument')
                text = argument.text.decode()
                if re.search(r'\bas\s+(std|core|alloc|serde_json|rusqlite|duckdb)\b', text):
                    return None
                if '*' in text and not text.startswith(('self::', 'super::', 'crate::', 'std::', 'core::', 'alloc::')):
                    return None
                if node.parent != tree and re.search(r'\b'+re.escape(leaf)+r'\b', text):
                    return None
            pending.extend(node.named_children)
        aliases = self.rust.aliases(path).get(leaf, set())
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
            origin = self.origin(path, name)
            if origin is None:
                return [], set(), set(), False
            macro = origin.split('::')[-1]
            record = {'path': path, 'side': side, 'line': location.start_point.row + 1,
                      'origin': origin, 'arguments_sha256': hashlib.sha256(arguments.text).hexdigest(),
                      'contract': 'external macro; all explicit argument identifiers retain their source dependencies'}
            if macro in FORMATTED:
                for node in arguments.named_children:
                    if node.type in ('string_literal', 'raw_string_literal') and not node.text.startswith(b'b'):
                        refs.update(format_captures(literal(node.text.decode())))
                record['implicit_captures'] = sorted(refs)
            if macro in ('concat', 'env'):
                record['constant'] = self.constant(path, macro, arguments)
            elif macro == 'include_str':
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
