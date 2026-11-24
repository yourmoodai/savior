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

from lockfile_evidence import npm_records
from rust_evidence import RustImports, use_bindings
from rust_macros import MacroDependencies
from javascript_evidence import declaration, deferred_body, import_bindings
from python_evidence import namespace_accesses, namespace_alias, passive_decorators, passive_definition

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


def declarations(root, language, scope='', wrappers=frozenset()):
    for node in root.named_children:
        yield node, scope
        definition = node.child_by_field_name('definition') or node
        if language == 'python' and definition.type == 'class_definition' and passive_definition(node, wrappers):
            name = definition.child_by_field_name('name').text.decode()
            yield from declarations(definition.child_by_field_name('body'), language, scope+name+'.', wrappers)
        if language == 'rust' and node.type in ('impl_item', 'mod_item'):
            body = node.child_by_field_name('body')
            target = node.child_by_field_name('type') if node.type == 'impl_item' else node.child_by_field_name('name')
            if body is not None and target is not None:
                yield from declarations(body, language, scope+target.text.decode()+'.')
        if language in ('javascript', 'typescript', 'tsx') and declaration(node).type == 'class_declaration':
            target = declaration(node)
            yield from declarations(target.child_by_field_name('body'), language,
                                    scope+target.child_by_field_name('name').text.decode()+'.')


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
            path = node.text.decode().removesuffix('::*')
            yield prefix + (tuple(path.split('::')) if path != '*' else ()) + ('*',), False
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
    macro_expansions: list = field(default_factory=list)


class Graph:
    def __init__(self, sources):
        self.sources, self.nodes, self.modules, self.edges, self.issues = sources, {}, {}, {}, {}
        self.external_imports = set()
        self.initializers = {}
        self.data_edges = {}
        self.containers, self.enclosing = {}, {}
        self.enclosing_scope = {}
        self.rust_sources = {side: RustImports(source, parser) for side, source in sources.items()}
        self.rust_macros = {side: MacroDependencies(rust) for side, rust in self.rust_sources.items()}
        self.literal_files = {}
        self.namespace_aliases = {}
        self.namespace_uses = {}

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
            lock_records = npm_records(path, source)
            if lock_records is not None:
                spans = [(start, end) for start, end, _ in lock_records]
            elif path.endswith('.json'):
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
            if lock_records is not None:
                metadata = [n for n, (_, _, kind) in zip(nodes, lock_records) if kind == 'metadata']
                for identity, (_, _, kind) in zip(nodes, lock_records):
                    self.data_edges[identity] = metadata if kind != 'metadata' else []
            return nodes
        root = parser(language).parse(data).root_node
        reflective_names = {'eval', 'exec', '__import__', 'getattr', 'importlib', 'builtins'}
        for imported in walk(root):
            module = imported.child_by_field_name('module_name')
            for name in imported.children_by_field_name('name') if imported.type in ('import_statement', 'import_from_statement') else []:
                origin = name.child_by_field_name('name') if name.type == 'aliased_import' else name
                alias = name.child_by_field_name('alias') if name.type == 'aliased_import' else None
                if origin is not None and ((module is not None and module.text in (b'importlib', b'builtins'))
                    or origin.text.decode() in reflective_names):
                    reflective_names.add((alias or origin).text.decode())
        if len(root.named_children) > 4096 or len(self.nodes)+len(root.named_children) > 16384:
            raise SourceError('source declarations exceed analysis capacity: '+path)
        nodes = []
        aliases = {name for n in root.named_children if (name := namespace_alias(n))} if language == 'python' else set()
        wrappers = passive_decorators(root, self.sources[side].files, path, walk) if language == 'python' else set()
        for node, scope in declarations(root, language, wrappers=wrappers):
            if node.type in ('comment', 'line_comment', 'block_comment'):
                continue
            definition = node
            if language == 'python' and node.type == 'decorated_definition' and passive_definition(node, wrappers):
                definition = node.child_by_field_name('definition')
            body = definition.child_by_field_name('body')
            javascript = language in ('javascript', 'typescript', 'tsx')
            if javascript and declaration(node).type == 'class_declaration':
                body = declaration(node).child_by_field_name('body')
            class_header = ((language == 'python' and definition.type == 'class_definition')
                            or (language == 'rust' and node.type in ('impl_item', 'mod_item') and body is not None)
                            or (javascript and declaration(node).type == 'class_declaration'))
            end_byte = body.start_byte if class_header else node.end_byte
            members = [n for n in walk(node) if not class_header or n.end_byte <= end_byte]
            text = data[node.start_byte:end_byte].decode()
            name_node = definition.child_by_field_name('name')
            names = {name_node.text.decode()} if name_node else set()
            if scope and name_node is not None:
                names = {scope+name_node.text.decode()}
            if node.type in ('import_statement', 'import_from_statement'):
                names = {(n.child_by_field_name('alias') or n.child_by_field_name('name') or n).text.decode().split('.')[0]
                         for n in node.children_by_field_name('name')}
                if javascript:
                    names = import_bindings(node)
            if node.type == 'use_declaration':
                names = {scope+name for name in use_bindings(node.child_by_field_name('argument'))}
            if node.type == 'enum_item':
                names.update(scope+name_node.text.decode()+'.'+variant.child_by_field_name('name').text.decode()
                             for variant in walk(node) if variant.type == 'enum_variant')
            if language == 'rust' and node.type == 'impl_item':
                names = {'@impl:' + scope + node.child_by_field_name('type').text.decode() + ':' +
                         (node.child_by_field_name('trait').text.decode() if node.child_by_field_name('trait') is not None else '')}
            if node.type in ('decorated_definition', 'export_statement'):
                names.update(scope+n.child_by_field_name('name').text.decode() for n in node.named_children
                             if n.child_by_field_name('name') is not None)
            if node.type in ('lexical_declaration', 'const_item', 'static_item'):
                names.update(n.child_by_field_name('name').text.decode() for n in walk(node)
                             if n.type == 'variable_declarator' and n.child_by_field_name('name'))
            refs = {n.text.decode() for n in members if n.type in ('identifier', 'type_identifier')}
            if language == 'rust':
                refs.update(scope+name for name in list(refs))
                for n in members:
                    if n.type in ('scoped_identifier', 'scoped_type_identifier'):
                        qualified = n.text.decode().replace('::', '.')
                        refs.add(scope+qualified.removeprefix('Self.') if qualified.startswith('Self.') else qualified)
                    elif n.type == 'field_expression':
                        receiver = n.child_by_field_name('value')
                        field = n.child_by_field_name('field')
                        if receiver is not None and field is not None and receiver.text == b'self':
                            refs.add(scope+field.text.decode())
            if scope and language == 'python':
                refs.update(scope+n.child_by_field_name('attribute').text.decode()
                            for n in members if n.type == 'attribute'
                            and n.child_by_field_name('object').text in (b'self', b'cls'))
            if javascript and scope:
                refs.update(scope+n.child_by_field_name('property').text.decode()
                            for n in members if n.type == 'member_expression'
                            and n.child_by_field_name('object') is not None
                            and n.child_by_field_name('object').text == b'this'
                            and n.child_by_field_name('property') is not None)
            import_nodes = [n.text.decode() for n in members if n.type in (
                'import_statement', 'import_from_statement', 'use_declaration')
                or (n.type == 'mod_item' and n.child_by_field_name('body') is None)
                or (n.type == 'export_statement' and n.child_by_field_name('source'))
                or (n.type == 'call_expression' and n.child_by_field_name('function') is not None
                    and n.child_by_field_name('function').text == b'require')]
            if language == 'rust':
                namespace_probe = Evidence('', path, side, node.start_point.row+1, node.end_point.row+1,
                                           text, names, refs, False)
                import_nodes.extend('use '+n.text.decode()+';' for n in members
                                    if n.type in ('scoped_identifier', 'scoped_type_identifier')
                                    and not n.text.startswith(b'Self::')
                                    and self.rust_sources[side].namespace(namespace_probe, n.text.decode().split('::')[0])
                                    and not any(parent.type in ('use_declaration', 'scoped_identifier', 'scoped_type_identifier')
                                                for parent in ancestors(n)))
            # Dispatch uncertainty is disclosed; runtime loading still requires missing source.
            parameters = set().union(*(parameter_names(n) for n in members
                                       if n.type in ('parameters', 'formal_parameters')))
            dynamic = False
            limitations = []
            expansions, literal_files = [], set()
            for call in members:
                if call.type == 'macro_invocation':
                    try:
                        expanded, captured, included, complete = self.rust_macros[side].expand(path, call, side)
                        expansions.extend(expanded)
                        refs.update(captured)
                        refs.update(scope+name for name in captured)
                        literal_files.update(included)
                        dynamic |= not complete
                    except (ValueError, TypeError, KeyError):
                        dynamic = True
                function = call.child_by_field_name('function') if call.type in ('call', 'call_expression') else None
                if javascript and function is not None and function.text == b'import':
                    dynamic = True
                if javascript and function is not None and function.text == b'require':
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
                                           for parent in ancestors(n))
                               and not (n.parent is not None and n.parent.type == 'attribute'
                                        and n.parent.child_by_field_name('attribute') == n)}
            dynamic |= bool(executable_refs & reflective_names)
            for call in members:
                function = call.child_by_field_name('function') if call.type in ('call', 'call_expression') else None
                if function is not None and function.text.decode() in parameters:
                    limitations.append({'path': path, 'side': side,
                        'line': call.start_point.row + 1, 'expression': function.text.decode(),
                        'reason': 'injected callback target is not statically established'})
            own_parameters = definition.child_by_field_name('parameters')
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
            self.enclosing_scope[identity] = scope
            evidence.macro_expansions = expansions
            self.literal_files[identity] = literal_files
            if language == 'rust':
                self.enclosing[identity] = [n for (s, p, prefix), n in self.containers.items()
                                           if s == side and p == path and scope.startswith(prefix)]
                if class_header:
                    target = node.child_by_field_name('type') if node.type == 'impl_item' else name_node
                    self.containers[side, path, scope+target.text.decode()+'.'] = identity
            if language == 'python':
                alias = namespace_alias(node)
                if alias and not scope:
                    self.namespace_aliases[identity] = alias
                init = replace(evidence, id=digest([identity, 'initialization']), initialization_only=True)
                if definition.type == 'function_definition':
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
                    init.refs.update(n.text.decode() for wrapper in node.named_children if wrapper.type == 'decorator'
                                     for n in walk(wrapper) if n.type == 'identifier')
                if scope:
                    evidence.refs.add(scope.rstrip('.'))
                    init.refs.add(scope.rstrip('.'))
                condition = node.child_by_field_name('condition')
                if (not scope and node.type == 'if_statement' and condition is not None
                    and condition.type == 'comparison_operator' and len(condition.children) == 3
                    and [n.text for n in condition.children[:2]] == [b'__name__', b'==']
                    and condition.children[2].text in (b"'__main__'", b'"__main__"')
                    and len(node.named_children) == 2):
                    # This branch is not entered when a module is imported.
                    consequence = node.child_by_field_name('consequence')
                    init = replace(init, text=data[node.start_byte:consequence.start_byte].decode(),
                        end=consequence.start_point.row+1, refs=set(), imports='', dynamic=False, limitations=[])
                self.nodes[init.id] = init
                for item in (evidence, init):
                    relevant = members if item.text == text else [n for n in members if n.end_byte <= node.start_byte + len(item.text.encode())]
                    self.namespace_uses[item.id] = {name: namespace_accesses(relevant, name) for name in aliases & item.refs}
                self.enclosing_scope[init.id] = scope
                self.initializers[identity] = init.id
            elif javascript:
                init = replace(evidence, id=digest([identity, 'initialization']), initialization_only=True)
                deferred = deferred_body(node)
                if deferred is not None:
                    cutoff = deferred.start_byte
                    header = [n for n in walk(node) if n.end_byte <= cutoff]
                    init = replace(init, text=data[node.start_byte:cutoff].decode(),
                                   end=deferred.start_point.row+1,
                                   refs={n.text.decode() for n in header if n.type in ('identifier', 'type_identifier')}-names,
                                   imports='', dynamic=False,
                                   limitations=[item for item in limitations if item['line'] <= deferred.start_point.row])
                    init.dynamic = bool(init.refs & reflective_names) or any(
                        n.type == 'call_expression' and n.child_by_field_name('function') is not None
                        and n.child_by_field_name('function').text in (b'import', b'require')
                        for n in header)
                if scope:
                    evidence.refs.add(scope.rstrip('.'))
                    init.refs.add(scope.rstrip('.'))
                self.nodes[init.id] = init
                self.enclosing_scope[init.id] = scope
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
        if suffix == '.rs':
            original = parser('rust').parse(text.encode()).root_node
            if original.named_children and all(n.type == 'mod_item' for n in original.named_children):
                declarations = [(('self', *parts), is_module) for parts, is_module in rust_imports(text)]
                resolved, missing, external = self.rust_sources[evidence.side].resolve(evidence, declarations)
                self.external_imports.update(external)
                if any(symbols not in ((), ('@module',)) for _, symbols in resolved):
                    missing.append('unresolved local module: ' + evidence.path)
                return [(p, ('@module',)) for p, symbols in resolved if symbols in ((), ('@module',))], missing
            resolved, missing, external = self.rust_sources[evidence.side].resolve(evidence, rust_imports(text))
            self.external_imports.update(external)
            return resolved, missing
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
                else:
                    variants += [name+ext for ext in ('.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx')]
                    variants += [name+'/index'+ext for ext in ('.js', '.ts', '.tsx')]
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
            self.edges[identity] = self.data_edges.get(identity, [])
            return self.edges[identity]
        siblings = self.module(evidence.side, evidence.path)
        required = set(self.enclosing.get(identity, []))
        selected_imports = []
        class_members = {}
        import_issues = []
        for path in self.literal_files.get(identity, []):
            literal = self.module(evidence.side, path)
            if not literal:
                source = self.sources[evidence.side].read(path)
                key = digest([self.sources[evidence.side].revision, path, 'literal', source])
                self.nodes[key] = Evidence(key, path, evidence.side, 1, source.count('\n')+1,
                                           source, set(), set(), False)
                self.modules[evidence.side, path] = literal = [key]
            required.update(literal)
        for sibling in siblings:
            other = self.nodes[sibling]
            if sibling in self.initializers:
                required.add(self.initializers[sibling])
                if other.names & evidence.refs:
                    alias = self.namespace_aliases.get(sibling)
                    selected = self.namespace_uses.get(identity, {}).get(alias)
                    if alias and selected:
                        paths, issues = self.imports(other)
                        import_issues.extend(issues)
                        selected_imports.extend((path, tuple(sorted(selected))) for path, _ in paths)
                        for path, _ in paths:
                            for name, methods in selected.items():
                                class_members.setdefault((path, name), set()).update(methods)
                    else:
                        required.add(sibling)
                if not evidence.initialization_only and any(name+method in other.names for name in evidence.refs for method in ('.__init__', '.__new__', '.__init_subclass__', '.constructor')):
                    required.add(sibling)
            elif sibling != identity and (other.names & evidence.refs or (not other.names
                  and self.enclosing_scope.get(identity, '').startswith(self.enclosing_scope.get(sibling, '')))):
                required.add(sibling)
        paths, missing = self.imports(evidence)
        paths.extend(selected_imports)
        missing.extend(import_issues)
        for path, symbols in paths:
            imported = self.module(evidence.side, path)
            if symbols == ('@module',):
                continue
            for n in imported:
                if n in self.initializers:
                    required.add(self.initializers[n])
                    if evidence.initialization_only and evidence.path.endswith('.py') and (path, symbols) not in selected_imports:
                        continue
                glob = any((s == '*' and any('.' not in name for name in self.nodes[n].names)) or
                           (s.endswith('.*') and any(name.startswith(s[:-1]) and '.' not in name[len(s)-1:]
                                                    for name in self.nodes[n].names)) for s in symbols)
                imported_scope = self.enclosing_scope.get(n, '')
                scope_applies = not imported_scope or any(s.startswith(imported_scope) for s in symbols)
                if not symbols or glob or (not self.nodes[n].names and n not in self.initializers and scope_applies) or self.nodes[n].names.intersection(symbols):
                    required.add(n)
                elif imported_scope and (methods := class_members.get((path, imported_scope.rstrip('.')))):
                    if '*' in methods or any(name.rsplit('.', 1)[-1] in methods or name.rsplit('.', 1)[-1].startswith('__')
                                             for name in self.nodes[n].names):
                        required.add(n)
                elif not evidence.initialization_only and any(name+method in self.nodes[n].names for name in symbols for method in ('.__init__', '.__new__', '.__init_subclass__', '.constructor')):
                    required.add(n)
            possible = set().union(*(self.nodes[n].names or self.nodes[n].refs for n in imported))
            if symbols and not any(s == '*' or s.endswith('.*') for s in symbols) and not set(symbols) <= possible:
                missing.append('unresolved imported binding: '+path+':'+','.join(sorted(set(symbols)-possible)))
        issues = self.issues.setdefault(identity, [])
        issues.extend(missing)
        if evidence.dynamic:
            issues.append('unresolved dynamic dependency: '+evidence.path+':'+str(evidence.start))
        # Duplicate definitions are conservative alternatives, never silently selected.
        for name in evidence.refs:
            bindings = [self.nodes[n] for n in siblings if name in self.nodes[n].names]
            if len(bindings) > 1 and not self.shared_python_namespace(name, bindings):
                issues.append('ambiguous definition: '+evidence.path+':'+name)
        self.edges[identity] = sorted(required-{identity})
        return self.edges[identity]

    @staticmethod
    def shared_python_namespace(name, bindings):
        for binding in bindings:
            if not binding.path.endswith('.py'):
                return False
            root = parser('python').parse(binding.text.encode()).root_node
            if len(root.named_children) != 1 or root.named_children[0].type != 'import_statement':
                return False
            imports = root.named_children[0].children_by_field_name('name')
            if not any(n.type != 'aliased_import' and n.text.decode().split('.')[0] == name for n in imports):
                return False
        return True

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
