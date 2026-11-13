"""Revision-bound syntax evidence; source is parsed as data, never executed."""
from contextvars import ContextVar
from dataclasses import dataclass
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


def parameter_names(parameters):
    names = set()
    for child in parameters.named_children:
        binding = child.child_by_field_name('name') or child.child_by_field_name('pattern')
        if binding is None and child.type == 'identifier':
            binding = child
        if binding is not None and binding.type == 'identifier':
            names.add(binding.text.decode())
    return names


def parser(language):
    from tree_sitter import Language, Parser
    if language in ('typescript', 'tsx'):
        import tree_sitter_typescript as grammar
        capsule = grammar.language_tsx() if language == 'tsx' else grammar.language_typescript()
    else:
        import importlib
        capsule = importlib.import_module('tree_sitter_'+language).language()
    return Parser(Language(capsule))


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


class Graph:
    def __init__(self, sources):
        self.sources, self.nodes, self.modules, self.edges, self.issues = sources, {}, {}, {}, {}
        self.external_imports = set()

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
        if len(root.named_children) > 4096 or len(self.nodes)+len(root.named_children) > 16384:
            raise SourceError('source declarations exceed analysis capacity: '+path)
        module_bindings = set()
        for declaration in root.named_children:
            if language == 'python' and declaration.type == 'import_statement':
                for name in declaration.named_children:
                    alias = name.child_by_field_name('alias')
                    module_bindings.add(alias.text.decode() if alias else name.text.decode().split('.')[0])
            elif language != 'python' and declaration.type == 'import_statement':
                module_bindings.update(n.text.decode() for parent in walk(declaration) if parent.type == 'namespace_import'
                                       for n in parent.named_children if n.type == 'identifier')
        nodes = []
        for node in root.named_children:
            if node.type in ('comment', 'line_comment', 'block_comment'):
                continue
            text = data[node.start_byte:node.end_byte].decode()
            name_node = node.child_by_field_name('name')
            names = {name_node.text.decode()} if name_node else set()
            if node.type in ('decorated_definition', 'export_statement'):
                names.update(n.child_by_field_name('name').text.decode() for n in node.named_children
                             if n.child_by_field_name('name') is not None)
            if node.type in ('lexical_declaration', 'const_item', 'static_item'):
                names.update(n.child_by_field_name('name').text.decode() for n in walk(node)
                             if n.type == 'variable_declarator' and n.child_by_field_name('name'))
            refs = {n.text.decode() for n in walk(node) if n.type in ('identifier', 'type_identifier')}
            import_nodes = [n.text.decode() for n in walk(node) if n.type in (
                'import_statement', 'import_from_statement', 'use_declaration')
                or (n.type == 'mod_item' and n.child_by_field_name('body') is None)
                or (n.type == 'export_statement' and n.child_by_field_name('source'))
                or (n.type == 'call_expression' and n.child_by_field_name('function') is not None
                    and n.child_by_field_name('function').text == b'require')]
            # Attribute dispatch, macros and runtime loading need evidence beyond syntax.
            parameters = set().union(*(parameter_names(n) for n in walk(node)
                                       if n.type in ('parameters', 'formal_parameters')))
            dynamic = False
            for call in walk(node):
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
                    known = receiver is not None and (receiver.type in ('string', 'string_literal') or (
                        receiver.type == 'identifier' and receiver.text.decode() in module_bindings-parameters))
                    dynamic |= not known
            dynamic |= bool(refs & {'eval', 'exec', '__import__', 'getattr', 'importlib'})
            dynamic |= any(n.type in ('call', 'call_expression') and n.child_by_field_name('function') is not None
                           and n.child_by_field_name('function').text.decode() in parameters for n in walk(node))
            own_parameters = node.child_by_field_name('parameters')
            if own_parameters is not None:
                refs -= parameter_names(own_parameters)
                # Defaults and annotations are evaluated outside parameter binding.
                refs.update(n.text.decode() for child in own_parameters.named_children
                            for field in ('value', 'type') if child.child_by_field_name(field) is not None
                            for n in walk(child.child_by_field_name(field)) if n.type in ('identifier', 'type_identifier'))
            identity = digest([self.sources[side].revision, path, node.start_byte, node.end_byte, text])
            evidence = Evidence(identity, path, side, node.start_point.row+1, node.end_point.row+1,
                                text, names, refs-names, dynamic, '\n'.join(import_nodes))
            self.nodes[identity] = evidence
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
            for match in re.finditer(r'(?m)^\s*(?:from\s+([.\w]+)\s+import\s+([^\n]+)|import\s+([^\n]+))', text):
                modules = [match[1]] if match[1] else [s.split(' as ')[0].strip() for s in match[3].split(',')]
                symbols = {s.strip().split(' as ')[0] for s in match[2].strip('()').split(',')} if match[2] else set()
                for module in modules:
                    relative = len(module)-len(module.lstrip('.'))
                    base = posixpath.dirname(path)
                    for _ in range(max(0, relative-1)):
                        base = posixpath.dirname(base)
                    name = module.lstrip('.').replace('.', '/')
                    if not name:
                        requests.extend((posixpath.join(base, s), '', True, set()) for s in symbols)
                    else:
                        requests.append((posixpath.join(base, name), '' if relative else name, bool(relative), symbols))
                if '(' in match[0] and ')' not in match[0]:
                    missing.append('unresolved multiline import: '+path)
        elif suffix == '.rs':
            for kind, module in re.findall(r'\b(use|mod)\s+([^;]+);', text):
                if any(char in module for char in '{}*'):
                    missing.append('unresolved grouped or glob import: '+path)
                    continue
                module = module.split(' as ')[0].strip()
                parts = module.split('::')
                if parts[0] in ('std', 'core', 'alloc'):
                    self.external_imports.add(module)
                    continue
                required = kind == 'mod' or parts[0] in ('crate', 'self', 'super')
                if parts[0] == 'crate':
                    base = path.rsplit('/src/', 1)[0]+'/src' if '/src/' in path else 'src' if path.startswith('src/') else ''
                    parts = parts[1:]
                else:
                    base = posixpath.dirname(path)
                    if parts[0] == 'self': parts = parts[1:]
                    while parts and parts[0] == 'super':
                        base, parts = posixpath.dirname(base), parts[1:]
                requests.append((posixpath.join(base, *parts), '', required, {parts[-1]} if len(parts)>1 else set()))
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
            if sibling != identity and (other.names & evidence.refs or not other.names):
                required.add(sibling)
        paths, missing = self.imports(evidence)
        for path, symbols in paths:
            imported = self.module(evidence.side, path)
            required.update(n for n in imported if not symbols or '*' in symbols or not self.nodes[n].names
                            or self.nodes[n].names.intersection(symbols))
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
        for anchor in unit.anchors():
            side = anchor['side']
            if side == 'file': continue
            path = old_paths[0] if side == 'old' and old_paths else unit.path
            if self.sources[side].read(path) is None:
                raise SourceError('source evidence unavailable: '+side+' '+path)
            for identity in self.module(side, path):
                node = self.nodes[identity]
                if node.start <= anchor['line'] <= node.end:
                    roots.add(identity)
        return roots
