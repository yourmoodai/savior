"""Revision-bound syntax evidence; source is parsed as data, never executed."""
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from importlib.metadata import version
from pathlib import PurePosixPath
import hashlib
import json
import posixpath
import re
import subprocess

snapshots = ContextVar('review_snapshots', default=None)
EXTENSIONS = {'.py': 'python', '.rs': 'rust', '.js': 'javascript', '.mjs': 'javascript',
              '.cjs': 'javascript', '.jsx': 'javascript', '.ts': 'typescript', '.tsx': 'tsx'}

class SourceError(ValueError):
    """Evidence cannot be established within the declared analysis limits."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def analyzer_versions():
    return {name: version(name) for name in ('tree-sitter', 'tree-sitter-python', 'tree-sitter-rust',
                                            'tree-sitter-javascript', 'tree-sitter-typescript')}


def git_snapshots(base, head):
    """Read regular blobs only; no worktree files, filters, links or submodules."""
    def git(*args):
        return subprocess.run(['git', *args], capture_output=True, check=True).stdout
    revisions = {'old': git('merge-base', base, head).decode().strip(),
                 'new': git('rev-parse', '--verify', head+'^{commit}').decode().strip()}
    result = {}
    for side, revision in revisions.items():
        files = {}
        for row in git('ls-tree', '-rz', '--full-tree', revision).split(b'\0'):
            if not row:
                continue
            metadata, path = row.split(b'\t', 1)
            mode, kind, oid = metadata.decode().split()
            path = path.decode('utf-8')
            if mode not in ('100644', '100755') or kind != 'blob':
                continue
            files[path] = oid
        result[side] = GitSnapshot(revision, files)
    return result


class GitSnapshot:
    def __init__(self, revision, files):
        self.revision, self.files, self.cache = revision, files, {}

    def read(self, path):
        if path not in self.files:
            return None
        if path not in self.cache:
            oid = self.files[path]
            size = int(subprocess.run(['git', 'cat-file', '-s', oid], capture_output=True, check=True).stdout)
            if size > 2_000_000:  # 2 MB maximum analyzed source blob
                raise SourceError('source blob exceeds analysis capacity: '+path)
            data = subprocess.run(['git', 'cat-file', 'blob', oid], capture_output=True, check=True).stdout
            try:
                self.cache[path] = data.decode('utf-8')
            except UnicodeDecodeError as error:
                raise SourceError('source is not utf-8: '+path) from error
        return self.cache[path]


class MemorySnapshot:
    def __init__(self, files):
        self.files = files
        self.revision = digest(files)

    def read(self, path):
        return self.files.get(path)


def fixture_snapshots(diff):
    """Trusted qualification fixtures supply complete miniature source files."""
    from chunk_plan import parse
    files = {'old': {}, 'new': {}}
    for unit in parse(diff):
        for side, markers in (('old', (' ', '-')), ('new', (' ', '+'))):
            files[side][unit.path] = files[side].get(unit.path, '')+''.join(r[1:] for r in unit.rows if r.startswith(markers))
    return {side: MemorySnapshot(value) for side, value in files.items()}


def walk(node):
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(reversed(current.named_children))


def ancestors(node):
    current = node.parent
    while current is not None:
        yield current
        current = current.parent


def parameter_names(parameters):
    names = set()
    for child in parameters.named_children:
        binding = child.child_by_field_name('name') or child.child_by_field_name('pattern')
        if binding is None and child.type == 'identifier':
            binding = child
        if binding is not None and binding.type == 'identifier':
            names.add(binding.text.decode())
    return names


def declarations(root, language, scope=''):
    for node in root.named_children:
        yield node, scope
        if language == 'python' and node.type == 'class_definition':
            name = node.child_by_field_name('name').text.decode()
            yield from declarations(node.child_by_field_name('body'), language, scope+name+'.')


def parser(language):
    from tree_sitter import Language, Parser
    if language in ('typescript', 'tsx'):
        import tree_sitter_typescript as grammar
        capsule = grammar.language_tsx() if language == 'tsx' else grammar.language_typescript()
    else:
        import tree_sitter_python
        import tree_sitter_rust
        import tree_sitter_javascript
        grammars = {'python': tree_sitter_python, 'rust': tree_sitter_rust,
                    'javascript': tree_sitter_javascript}
        capsule = grammars[language].language()
    return Parser(Language(capsule))


def python_imports(text):
    root = parser('python').parse(text.encode()).root_node
    for node in root.named_children:
        module = node.child_by_field_name('module_name')
        names = [n.child_by_field_name('name') if n.type == 'aliased_import' else n
                 for n in node.children_by_field_name('name')]
        if node.type == 'import_from_statement':
            yield module.text.decode(), {n.text.decode() for n in names} or {'*'}
        elif node.type == 'import_statement':
            for name in names:
                yield name.text.decode(), set()


def rust_imports(text):
    def expand(node, prefix=()):
        if node.type == 'scoped_use_list':
            path = node.child_by_field_name('path')
            base = prefix+tuple(path.text.decode().split('::')) if path else prefix
            yield from expand(node.child_by_field_name('list'), base)
        elif node.type == 'use_list':
            for child in node.named_children:
                if child.type not in ('line_comment', 'block_comment'):
                    yield from expand(child, prefix)
        elif node.type == 'use_as_clause':
            yield from expand(node.child_by_field_name('path'), prefix)
        elif node.type == 'use_wildcard':
            yield None, False
        else:
            parts = tuple(node.text.decode().split('::'))
            if parts == ('self',) and prefix:
                yield prefix, True
            else:
                yield prefix+parts, False
    root = parser('rust').parse(text.encode()).root_node
    for node in root.named_children:
        if node.type == 'use_declaration':
            yield from expand(node.child_by_field_name('argument'))
        elif node.type == 'mod_item':
            yield (node.child_by_field_name('name').text.decode(),), True


@dataclass
class Evidence:
    id: str
    path: str
    side: str
    start: int
    end: int
    text: str
    names: set
    refs: set
    dynamic: bool
    imports: str = ''
    limitations: list = field(default_factory=list)
    initialization_only: bool = False


class Graph:
    def __init__(self, sources):
        self.sources, self.nodes, self.modules, self.edges, self.issues = sources, {}, {}, {}, {}
        self.external_imports = set()
        self.initializers = {}

    def module(self, side, path):
        key = (side, path)
        if key in self.modules:
            return self.modules[key]
        source = self.sources[side].read(path)
        if source is None:
            self.modules[key] = []
            return []
        data = source.encode()
        language = EXTENSIONS.get(PurePosixPath(path).suffix)
        if not language:
            spans = []
            if path.endswith('.json'):
                try:
                    parsed = json.loads(source)
                    if isinstance(parsed, list):
                        cursor = source.index('[')+1
                        decoder = json.JSONDecoder()
                        for _ in parsed:
                            while source[cursor].isspace() or source[cursor] == ',': cursor += 1
                            _, end = decoder.raw_decode(source, cursor)
                            spans.append((cursor, end))
                            cursor = end
                    else:
                        spans = [(0, len(source))]
                except (ValueError, IndexError):
                    spans = [(0, len(source))]
            elif path.endswith('.md'):
                starts = sorted({0, *[m.start() for m in re.finditer(r'(?m)^#{1,6} ', source)], len(source)})
                spans = list(zip(starts, starts[1:]))
            nodes = []
            for start, end in spans:
                text = source[start:end]
                identity = digest([self.sources[side].revision, path, start, end, text])
                self.nodes[identity] = Evidence(identity, path, side, source[:start].count('\n')+1,
                    source[:end].count('\n')+1, text, set(), set(), False)
                nodes.append(identity)
            self.modules[key] = nodes
            return nodes
        root = parser(language).parse(data).root_node
        reflective_names = {'eval', 'exec', '__import__', 'getattr', 'importlib'}
        for imported in walk(root):
            module = imported.child_by_field_name('module_name')
            for name in imported.children_by_field_name('name') if imported.type in ('import_statement', 'import_from_statement') else []:
                origin = name.child_by_field_name('name') if name.type == 'aliased_import' else name
                alias = name.child_by_field_name('alias') if name.type == 'aliased_import' else None
                if origin is not None and ((module is not None and module.text == b'importlib')
                    or origin.text.decode() in reflective_names):
                    reflective_names.add((alias or origin).text.decode())
        if len(root.named_children) > 4096 or len(self.nodes)+len(root.named_children) > 16384:
            raise SourceError('source declarations exceed analysis capacity: '+path)
        nodes = []
        for node, scope in declarations(root, language):
            if node.type in ('comment', 'line_comment', 'block_comment'):
                continue
            body = node.child_by_field_name('body')
            class_header = language == 'python' and node.type == 'class_definition'
            end_byte = body.start_byte if class_header else node.end_byte
            members = [n for n in walk(node) if not class_header or n.end_byte <= end_byte]
            text = data[node.start_byte:end_byte].decode()
            name_node = node.child_by_field_name('name')
            names = {name_node.text.decode()} if name_node else set()
            if scope and name_node is not None:
                names = {scope+name_node.text.decode()}
            if node.type in ('import_statement', 'import_from_statement'):
                names = {(n.child_by_field_name('alias') or n.child_by_field_name('name') or n).text.decode().split('.')[0]
                         for n in node.children_by_field_name('name')}
            if node.type in ('decorated_definition', 'export_statement'):
                names.update(scope+n.child_by_field_name('name').text.decode() for n in node.named_children
                             if n.child_by_field_name('name') is not None)
            if node.type in ('lexical_declaration', 'const_item', 'static_item'):
                names.update(n.child_by_field_name('name').text.decode() for n in walk(node)
                             if n.type == 'variable_declarator' and n.child_by_field_name('name'))
            refs = {n.text.decode() for n in members if n.type in ('identifier', 'type_identifier')}
            if scope:
                refs.update(scope+n.child_by_field_name('attribute').text.decode()
                            for n in members if n.type == 'attribute'
                            and n.child_by_field_name('object').text in (b'self', b'cls'))
            import_nodes = [n.text.decode() for n in members if n.type in (
                'import_statement', 'import_from_statement', 'use_declaration')
                or (n.type == 'mod_item' and n.child_by_field_name('body') is None)
                or (n.type == 'export_statement' and n.child_by_field_name('source'))
                or (n.type == 'call_expression' and n.child_by_field_name('function') is not None
                    and n.child_by_field_name('function').text == b'require')]
            # Dispatch uncertainty is disclosed; runtime loading still requires missing source.
            parameters = set().union(*(parameter_names(n) for n in members
                                       if n.type in ('parameters', 'formal_parameters')))
            dynamic = False
            limitations = []
            for call in members:
                if call.type == 'macro_invocation':
                    dynamic = True
                function = call.child_by_field_name('function') if call.type in ('call', 'call_expression') else None
                if function is not None and function.text == b'import':
                    dynamic = True
                if function is not None and function.text == b'require':
                    arguments = call.child_by_field_name('arguments')
                    dynamic |= arguments is None or len(arguments.named_children) != 1 or arguments.named_children[0].type != 'string'
                if function is not None and function.type in ('attribute', 'member_expression', 'field_expression'):
                    receiver = function.child_by_field_name('object') or function.child_by_field_name('value')
                    if receiver is None or receiver.type not in ('string', 'string_literal'):
                        limitations.append({'path': path, 'side': side,
                            'line': call.start_point.row + 1,
                            'expression': function.text.decode(),
                            'reason': 'runtime receiver target is not statically established'})
            # An import declaration is not reflective execution. Keep references
            # (including aliases passed as values) conservative outside imports.
            executable_refs = {n.text.decode() for n in members
                               if n.type in ('identifier', 'type_identifier')
                               and not any(parent.type in ('import_statement', 'import_from_statement')
                                           for parent in ancestors(n))}
            dynamic |= bool(executable_refs & reflective_names)
            for call in members:
                function = call.child_by_field_name('function') if call.type in ('call', 'call_expression') else None
                if function is not None and function.text.decode() in parameters:
                    limitations.append({'path': path, 'side': side,
                        'line': call.start_point.row + 1, 'expression': function.text.decode(),
                        'reason': 'injected callback target is not statically established'})
            own_parameters = node.child_by_field_name('parameters')
            if own_parameters is not None:
                refs -= parameter_names(own_parameters)
                # Defaults and annotations are evaluated outside parameter binding.
                refs.update(n.text.decode() for child in own_parameters.named_children
                            for field in ('value', 'type') if child.child_by_field_name(field) is not None
                            for n in walk(child.child_by_field_name(field)) if n.type in ('identifier', 'type_identifier'))
            identity = digest([self.sources[side].revision, path, node.start_byte, node.end_byte, text])
            evidence = Evidence(identity, path, side, node.start_point.row+1, body.start_point.row+1 if class_header else node.end_point.row+1,
                                text, names, refs-names, dynamic, '\n'.join(import_nodes), limitations)
            self.nodes[identity] = evidence
            if language == 'python':
                init = replace(evidence, id=digest([identity, 'initialization']), initialization_only=True)
                if node.type == 'function_definition':
                    body = node.child_by_field_name('body')
                    header_nodes = [n for n in walk(node) if n.end_byte <= body.start_byte]
                    init = replace(init, text=data[node.start_byte:body.start_byte].decode(),
                        end=body.start_point.row+1,
                        refs={n.text.decode() for n in header_nodes if n.type == 'identifier'}-names,
                        imports='', dynamic=False,
                        limitations=[item for item in limitations if item['line'] <= body.start_point.row])
                    # Defaults and annotations execute when the definition is bound.
                    init.dynamic = bool(init.refs & reflective_names)
                    if own_parameters is not None:
                        init.refs -= parameter_names(own_parameters)
                        init.refs.update(n.text.decode() for child in own_parameters.named_children
                            for field in ('value', 'type') if child.child_by_field_name(field) is not None
                            for n in walk(child.child_by_field_name(field)) if n.type == 'identifier')
                if scope:
                    evidence.refs.add(scope.rstrip('.'))
                    init.refs.add(scope.rstrip('.'))
                condition = node.child_by_field_name('condition')
                if (not scope and node.type == 'if_statement' and condition is not None
                    and condition.text.decode() in ("__name__ == '__main__'", '__name__ == "__main__"')
                    and len(node.named_children) == 2):
                    # This branch is not entered when a module is imported.
                    consequence = node.child_by_field_name('consequence')
                    init = replace(init, text=data[node.start_byte:consequence.start_byte].decode(),
                        end=consequence.start_point.row+1, refs=set(), imports='', dynamic=False, limitations=[])
                self.nodes[init.id] = init
                self.initializers[identity] = init.id
            nodes.append(identity)
        self.modules[key] = nodes
        if root.has_error:
            for identity in nodes:
                self.issues.setdefault(identity, []).append('syntax analysis incomplete: '+path)
        return nodes

    def imports(self, evidence):
        path, text = evidence.path, evidence.imports
        suffix = PurePosixPath(path).suffix
        requests, missing = [], []
        if suffix == '.py':
            for module, symbols in python_imports(text):
                if '*' in symbols:
                    missing.append('unresolved glob import: '+path)
                relative = len(module)-len(module.lstrip('.'))
                base = posixpath.dirname(path)
                for _ in range(max(0, relative-1)):
                    base = posixpath.dirname(base)
                name = module.lstrip('.').replace('.', '/')
                if not name:
                    requests.extend((posixpath.join(base, s), '', True, set()) for s in symbols if s != '*')
                else:
                    requests.append((posixpath.join(base, name), '' if relative else name, bool(relative), symbols))
        elif suffix == '.rs':
            for imported, is_module in rust_imports(text):
                if imported is None:
                    missing.append('unresolved glob import: '+path)
                    continue
                module = '::'.join(imported)
                parts = list(imported)
                if parts[0] in ('std', 'core', 'alloc'):
                    self.external_imports.add(module)
                    continue
                required = is_module or parts[0] in ('crate', 'self', 'super')
                if parts[0] == 'crate':
                    base = path.rsplit('/src/', 1)[0]+'/src' if '/src/' in path else 'src' if path.startswith('src/') else ''
                    parts = parts[1:]
                else:
                    base = posixpath.dirname(path)
                    if parts[0] == 'self': parts = parts[1:]
                    while parts and parts[0] == 'super':
                        base, parts = posixpath.dirname(base), parts[1:]
                requests.append((posixpath.join(base, *parts), '', required,
                                 {parts[-1]} if len(parts)>1 and not is_module else set()))
        else:
            for module in re.findall(r'''(?:from\s*|require\s*\(\s*|import\s*\(\s*|import\s*)['"]([^'"]+)['"]''', text):
                selected = re.search(r'\{([^}]+)\}', text)
                symbols = {s.strip().split(' as ')[0] for s in selected[1].split(',')} if selected else set()
                requests.append((posixpath.normpath(posixpath.join(posixpath.dirname(path), module)), '', module.startswith('.'), symbols))
        result = []
        files = self.sources[evidence.side].files
        for local, absolute, required, symbols in requests:
            candidates = []
            for name in (local, absolute):
                if not name or name.startswith('../') or name.startswith('/'):
                    continue
                variants = [name]
                if suffix == '.py':
                    variants += [name+'.py', name+'/__init__.py']
                elif suffix == '.rs':
                    variants += [name+'.rs', name+'/mod.rs']
                else:
                    variants += [name+ext for ext in ('.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx')]
                    variants += [name+'/index'+ext for ext in ('.js', '.ts', '.tsx')]
                if suffix == '.rs' and '/' in name:
                    variants += [name.rsplit('/', 1)[0]+'.rs']
                candidates += [p for p in variants if p in files]
            candidates = sorted(set(candidates))
            if len(candidates) > 1:
                missing.append('ambiguous local import: '+local)
            result.extend((p, tuple(sorted(symbols))) for p in candidates)
            if required and not candidates:
                missing.append('unresolved local import: '+local)
            elif not candidates:
                self.external_imports.add(absolute or local)
        return sorted(set(result)), missing

    def dependencies(self, identity):
        if identity in self.edges:
            return self.edges[identity]
        evidence = self.nodes[identity]
        if PurePosixPath(evidence.path).suffix not in EXTENSIONS:
            self.edges[identity] = []
            return []
        siblings = self.module(evidence.side, evidence.path)
        required = set()
        for sibling in siblings:
            other = self.nodes[sibling]
            if sibling in self.initializers:
                required.add(self.initializers[sibling])
                if other.names & evidence.refs:
                    required.add(sibling)
                if not evidence.initialization_only and any(name+method in other.names for name in evidence.refs for method in ('.__init__', '.__new__', '.__init_subclass__')):
                    required.add(sibling)
            elif sibling != identity and (other.names & evidence.refs or not other.names):
                required.add(sibling)
        paths, missing = self.imports(evidence)
        for path, symbols in paths:
            imported = self.module(evidence.side, path)
            for n in imported:
                if n in self.initializers:
                    required.add(self.initializers[n])
                    if evidence.initialization_only and evidence.path.endswith('.py'):
                        continue
                if not symbols or '*' in symbols or (not self.nodes[n].names and n not in self.initializers) or self.nodes[n].names.intersection(symbols):
                    required.add(n)
                elif not evidence.initialization_only and any(name+method in self.nodes[n].names for name in symbols for method in ('.__init__', '.__new__', '.__init_subclass__')):
                    required.add(n)
            possible = set().union(*(self.nodes[n].names or self.nodes[n].refs for n in imported))
            if symbols and '*' not in symbols and not set(symbols) <= possible:
                missing.append('unresolved imported binding: '+path+':'+','.join(sorted(set(symbols)-possible)))
        issues = self.issues.setdefault(identity, [])
        issues.extend(missing)
        if evidence.dynamic:
            issues.append('unresolved dynamic dependency: '+evidence.path+':'+str(evidence.start))
        # Duplicate definitions are conservative alternatives, never silently selected.
        for name in evidence.refs:
            if sum(name in self.nodes[n].names for n in siblings) > 1:
                issues.append('ambiguous definition: '+evidence.path+':'+name)
        self.edges[identity] = sorted(required-{identity})
        return self.edges[identity]

    def closure(self, roots):
        pending, visited, issues = list(sorted(roots)), set(), []
        while pending:
            identity = pending.pop()
            if identity in visited:
                continue
            visited.add(identity)
            if len(visited) > 4096:
                raise SourceError('dependency closure exceeds analysis capacity')
            pending.extend(self.dependencies(identity))
            issues.extend(self.issues.get(identity, []))
        return sorted(visited), sorted(set(issues))

    def roots(self, unit):
        roots = set()
        old_paths = re.findall(r'^--- a/(.*)$', unit.header, re.M)
        changed = {f'{unit.id.split("/")[0]}:{offset}' for offset, row in enumerate(unit.rows, unit.start)
                   if row.startswith(('+', '-'))}
        for anchor in unit.anchors():
            side = anchor['side']
            if side == 'file': continue
            if anchor['id'] not in changed:
                continue
            path = old_paths[0] if side == 'old' and old_paths else unit.path
            if self.sources[side].read(path) is None:
                raise SourceError('source evidence unavailable: '+side+' '+path)
            for identity in self.module(side, path):
                node = self.nodes[identity]
                if node.start <= anchor['line'] <= node.end:
                    roots.add(identity)
        return roots
