"""Resolve Rust source modules and Cargo path dependencies from frozen files."""
import json
import posixpath
import re
import tomllib
from dataclasses import replace


def use_bindings(node):
    if node.type == 'use_as_clause':
        alias = node.child_by_field_name('alias')
        return {alias.text.decode()} if alias is not None else set()
    if node.type == 'use_wildcard':
        return set()
    if node.type == 'scoped_use_list':
        result = use_bindings(node.child_by_field_name('list'))
        if 'self' in result:
            result.remove('self')
            result.add(node.child_by_field_name('path').text.decode().split('::')[-1])
        return result
    if node.type == 'use_list':
        return set().union(*(use_bindings(n) for n in node.named_children))
    return {node.text.decode().split('::')[-1]}


class RustImports:
    def __init__(self, snapshot, parse):
        self.snapshot, self.parse = snapshot, parse
        self.trees, self.root_cache = {}, {}
        self.routes = {}
        self.alias_cache = {}

    def tree(self, path):
        if path not in self.trees:
            source = self.snapshot.read(path)
            self.trees[path] = self.parse('rust').parse(source.encode()).root_node if source is not None else None
        return self.trees[path]

    def manifest(self, path):
        directory = posixpath.dirname(path)
        while True:
            file = posixpath.join(directory, 'Cargo.toml')
            source = self.snapshot.read(file)
            if source is not None:
                return directory, tomllib.loads(source)
            if not directory:
                return '', {}
            directory = posixpath.dirname(directory)

    def child(self, file, name):
        tree = self.tree(file)
        attributes = []
        declared = False
        if tree is not None:
            for node in tree.named_children:
                if node.type == 'attribute_item':
                    attributes.append(node.text.decode())
                    continue
                if node.type == 'mod_item' and node.child_by_field_name('name').text.decode() == name:
                    declared = True
                    if node.child_by_field_name('body') is not None:
                        return [(file, name)]
                    explicit = re.findall(r'#\[path\s*=\s*("[^"\n]+")\]', '\n'.join(attributes))
                    if explicit:
                        candidate = posixpath.normpath(posixpath.join(posixpath.dirname(file), json.loads(explicit[-1])))
                        return [(candidate, '')] if not candidate.startswith('../') and candidate in self.snapshot.files else []
                    break
                attributes = []
        if not declared:
            return []
        directory = posixpath.dirname(file)
        stem = posixpath.basename(file).removesuffix('.rs')
        if stem not in ('lib', 'main', 'mod'):
            directory = posixpath.join(directory, stem)
        candidates = [posixpath.join(directory, name + '.rs'), posixpath.join(directory, name, 'mod.rs')]
        return [(p, '') for p in candidates if p in self.snapshot.files]

    def roots(self, path):
        if path in self.root_cache:
            return self.root_cache[path]
        directory, manifest = self.manifest(path)
        prefix = directory + '/' if directory else ''
        if not manifest and '/src/' in path:
            prefix = path.split('/src/', 1)[0] + '/'
        candidates = {prefix + 'src/lib.rs', prefix + 'src/main.rs'}
        candidates.update(prefix + b['path'] for b in manifest.get('bin', []) if 'path' in b)
        candidates.update(p for p in self.snapshot.files if p.startswith(prefix + 'src/bin/') and
                          (p.count('/') == (prefix + 'src/bin/').count('/') or p.endswith('/main.rs')) and p.endswith('.rs'))
        if 'path' in manifest.get('lib', {}):
            candidates.add(prefix + manifest['lib']['path'])
        found = []
        for candidate in sorted(candidates & self.snapshot.files.keys()):
            pending, visited = [(candidate, [])], set()
            while pending:
                current, parents = pending.pop()
                if current in visited:
                    continue
                visited.add(current)
                if current == path:
                    found.append(candidate)
                    self.routes[candidate, path] = parents
                    break
                tree = self.tree(current)
                if tree is not None:
                    for n in tree.named_children:
                        if n.type == 'mod_item' and n.child_by_field_name('body') is None:
                            pending.extend((p, parents + [current]) for p, scope in self.child(current, n.child_by_field_name('name').text.decode()) if not scope)
        self.root_cache[path] = found
        return found

    def workspace_dependency(self, path, name):
        directory, manifest = self.manifest(path)
        dependencies = []
        for table in [manifest, *manifest.get('target', {}).values()]:
            for section in ['dependencies', 'dev-dependencies', 'build-dependencies']:
                dependencies.extend((key, value) for key, value in table.get(section, {}).items()
                                    if key.replace('-', '_') == name)
        targets, local = set(), False
        for key, dependency in dependencies:
            base = directory
            if isinstance(dependency, dict) and dependency.get('workspace'):
                current = directory
                while True:
                    source = self.snapshot.read(posixpath.join(current, 'Cargo.toml'))
                    workspace = tomllib.loads(source).get('workspace', {}) if source is not None else {}
                    if key in workspace.get('dependencies', {}):
                        dependency, base = workspace['dependencies'][key], current
                        break
                    if not current:
                        break
                    current = posixpath.dirname(current)
            if not isinstance(dependency, dict) or 'path' not in dependency:
                continue
            local = True
            target = posixpath.normpath(posixpath.join(base, dependency['path']))
            if target.startswith('../') or target.startswith('/'):
                return []
            source = self.snapshot.read(posixpath.join(target, 'Cargo.toml'))
            library = tomllib.loads(source).get('lib', {}).get('path', 'src/lib.rs') if source is not None else 'src/lib.rs'
            targets.add(posixpath.normpath(posixpath.join(target, library)))
        return sorted(targets) if local else None

    def aliases(self, path):
        if path in self.alias_cache:
            return self.alias_cache[path]
        result = {}
        tree = self.tree(path)
        if tree is None:
            return result

        def descend(node, prefix=()):
            if node.type == 'scoped_use_list':
                path_node = node.child_by_field_name('path')
                yield from descend(node.child_by_field_name('list'), prefix + tuple(path_node.text.decode().split('::')))
            elif node.type == 'use_list':
                for child in node.named_children:
                    yield from descend(child, prefix)
            elif node.type == 'use_as_clause':
                origin = prefix + tuple(node.child_by_field_name('path').text.decode().split('::'))
                yield node.child_by_field_name('alias').text.decode(), origin
            elif node.type not in ('use_wildcard', 'line_comment', 'block_comment'):
                origin = prefix + tuple(node.text.decode().split('::'))
                yield (prefix[-1], prefix) if origin[-1] == 'self' and prefix else (origin[-1], origin)

        for node in tree.named_children:
            if node.type == 'use_declaration':
                for alias, origin in descend(node.child_by_field_name('argument')):
                    if origin != (alias,):
                        result.setdefault(alias, set()).add(origin)
        self.alias_cache[path] = result
        return result

    def inline_scope(self, evidence):
        result = []
        node = self.tree(evidence.path)
        while node is not None:
            matches = [n for n in node.named_children if n.type == 'mod_item'
                       and n.child_by_field_name('body') is not None
                       and n.start_point.row + 1 <= evidence.start <= n.end_point.row + 1]
            if not matches:
                break
            result.append(matches[0].child_by_field_name('name').text.decode())
            node = matches[0].child_by_field_name('body')
        return result

    def namespace(self, evidence, name):
        if name in ('crate', 'self', 'super'):
            return True
        tree = self.tree(evidence.path)
        if tree is not None and any(n.type in ('struct_item', 'enum_item', 'type_item', 'trait_item', 'union_item')
                and n.child_by_field_name('name').text.decode() == name for n in tree.named_children):
            return False
        resolved, missing, external = self.resolve(evidence, [((name,), False)])
        return bool(external) or any(symbols == ('@module',) for _, symbols in resolved)

    def resolve(self, evidence, declarations, seen=()):
        requests, missing, external = [], [], []
        for imported, is_module in declarations:
            if imported is None:
                missing.append('unresolved glob import: ' + evidence.path)
                continue
            parts = list(imported)
            wildcard = parts[-1] == '*'
            if wildcard:
                parts.pop()
            qualified = '::'.join(parts)
            aliases = self.aliases(evidence.path).get(parts[0], set())
            binding = (evidence.path, parts[0])
            if aliases and binding in seen:
                missing.append('cyclic rust import binding: ' + qualified)
                continue
            if aliases:
                for alias in aliases:
                    expanded = tuple(alias) + tuple(parts[1:]) + (('*',) if wildcard else ())
                    found, gaps, libraries = self.resolve(evidence, [(expanded, is_module)], (*seen, binding))
                    requests.extend(found)
                    missing.extend(gaps)
                    external.extend(libraries)
                continue
            if parts[0] in ('std', 'core', 'alloc'):
                external.append(qualified)
                continue
            roots = self.roots(evidence.path)
            initial_scope = ''
            if parts[0] == 'crate':
                origins = roots
                parts.pop(0)
            elif parts[0] in ('self', 'super'):
                origins = [evidence.path]
                inline = self.inline_scope(evidence)
                if parts.pop(0) == 'super':
                    if inline:
                        inline.pop()
                    else:
                        origins = [self.routes[root, evidence.path][-1] for root in roots
                                   if self.routes[root, evidence.path]]
                initial_scope = '.'.join(inline) + '.' if inline else ''
            else:
                dependency = self.workspace_dependency(evidence.path, parts[0])
                if dependency is not None:
                    origins = dependency
                    parts.pop(0)
                else:
                    origins = [evidence.path]
                    if roots:
                        origins = roots
                        if not is_module and not any(self.child(root, parts[0]) for root in roots):
                            external.append(qualified)
                            continue
                    elif not is_module:
                        directory = posixpath.dirname(evidence.path)
                        if not any(posixpath.join(directory, parts[0] + suffix) in self.snapshot.files for suffix in ('.rs', '/mod.rs')):
                            external.append(qualified)
                            continue
            resolved = []
            for origin in origins:
                current, scope, remaining = origin, initial_scope, list(parts)
                while remaining:
                    children = self.child(current, remaining[0]) if not scope else []
                    if len(children) > 1:
                        missing.append('ambiguous local import: ' + qualified)
                        break
                    if not children:
                        break
                    current, nested = children[0]
                    scope += nested + '.' if nested else ''
                    remaining.pop(0)
                if current in self.snapshot.files:
                    exported = self.aliases(current).get(remaining[0], set()) if remaining else set()
                    if exported and current != evidence.path:
                        found, gaps, libraries = self.resolve(replace(evidence, path=current),
                            [(tuple(remaining) + (('*',) if wildcard else ()), is_module)], seen)
                        resolved.extend(found)
                        missing.extend(gaps)
                        external.extend(libraries)
                        continue
                    symbols = {scope + '.'.join([*remaining, '*'] if wildcard else remaining)} if remaining or wildcard else {'@module'}
                    resolved.append((current, tuple(sorted(symbols))))
            if not resolved and not roots:
                base = evidence.path.rsplit('/src/', 1)[0] + '/src' if '/src/' in evidence.path else 'src' if evidence.path.startswith('src/') else ''
                if imported[0] == 'self':
                    base = posixpath.dirname(evidence.path)
                elif imported[0] == 'super':
                    base = posixpath.dirname(posixpath.dirname(evidence.path))
                local = posixpath.join(base, *parts)
                candidates = [local + '.rs', local + '/mod.rs', posixpath.dirname(local) + '.rs']
                resolved = [(p, ('*',) if wildcard else (parts[-1],) if len(parts) > 1 and not is_module else ()) for p in candidates if p in self.snapshot.files]
            if resolved:
                requests.extend(resolved)
            else:
                missing.append('unresolved local import: ' + qualified)
        return sorted(set(requests)), missing, external
