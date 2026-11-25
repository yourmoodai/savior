"""One immutable evidence plan for offline admission and subsequent execution."""
from collections import Counter
from contextvars import ContextVar
from dataclasses import asdict
import json

from chunk_plan import Unit, anchors, parse, ASSESSMENT_LIMIT
from source_graph import Graph, SourceError, analyzer_versions, digest
from source_context import source_context
from bedrock_client import MAX_REQUEST_BYTES
from egm import FOCUS
from model_catalog import input_capacity

SCHEMA = 6
frozen_execution = ContextVar('frozen_review_plan', default=None)


class PlanningError(ValueError):
    pass


def verification_messages(messages):
    from adversarial_review import VERIFICATION_PROMPT
    from prompt_profiles import prompts
    from verification import SCHEMA as verification_schema
    # UTF-8 JSON needs at most six bytes per character (escaped controls).
    finding = {'finding_id': 'f'*64, 'location': 999999999, **{k: '\x00'*600 for k in ('invariant', 'scenario', 'correction')}}
    expanded = [dict(m) for m in messages]
    expanded[-1]['content'] += '\n'+prompts('', VERIFICATION_PROMPT)[1]+'\nCandidate findings to verify: '+json.dumps([finding], ensure_ascii=False)+'\n'
    expanded[-1]['content'] += '\nReviewer focus: '+max(FOCUS.values(), key=len)
    expanded[-1]['content'] += json.dumps(verification_schema)
    return expanded


def capacities():
    return min(input_capacity(role) for role in FOCUS), input_capacity('verification')


def verification_bound(messages):
    from bedrock_client import estimate_input_units
    return estimate_input_units(verification_messages(messages))


def within_capacity(chunk, limits):
    return (chunk['input_tokens'] <= limits[0] and chunk['verification_input_units'] <= limits[1]
            and chunk['request_bytes_bound'] <= MAX_REQUEST_BYTES)


def limitation_context(items):
    sources = sorted({(i['side'], i['path']) for i in items})
    locations = {source: index for index, source in enumerate(sources)}
    expressions = {}
    for item in items:
        key = ('callback' if item['reason'].startswith('injected') else 'receiver', item['expression'])
        source = locations[item['side'], item['path']]
        expressions.setdefault(key, {}).setdefault(source, set()).add(item['line'])
    calls = [[kind, expr, {i: sorted(lines) for i, lines in sorted(locations.items())}]
             for (kind, expr), locations in sorted(expressions.items())]
    return json.dumps({'sources': sources, 'calls': calls}, separators=(',', ':'))


def macro_context(items):
    sources = sorted({(i['side'], i['path']) for i in items})
    locations = {source: index for index, source in enumerate(sources)}
    grouped = {}
    for item in items:
        contract = {k: v for k, v in item.items() if k not in ('side', 'path', 'line')}
        key = json.dumps(contract, sort_keys=True, separators=(',', ':'))
        grouped.setdefault(key, set()).add((locations[item['side'], item['path']], item['line']))
    return json.dumps({'sources': sources, 'expansions': [
        {'locations': sorted(grouped[key]), **json.loads(key)} for key in sorted(grouped)]},
        separators=(',', ':'))


def pack(diff, count, messages, sources, strategy):
    graph = Graph(sources)
    original = parse(diff)
    limits = capacities()
    chunks, pending = [], sorted(original, key=lambda u: (u.path, u.start))
    source_fits, subdivisions = {}, 0

    def request(units, roots=None):
        roots = {n for u in units for n in graph.roots(u)} if roots is None else roots
        required, issues = graph.closure(roots)
        support = source_context(graph, required)
        limitations = [item for n in required for item in graph.nodes[n].limitations]
        expansions = [item for n in required for item in graph.nodes[n].macro_expansions]
        if expansions:
            support += '\nBounded external macro dependency expansions (locations: source index, line):\n' + macro_context(expansions)
        if limitations:
            support += '\nRuntime analysis limitations (source coverage is not runtime proof):\n'
            support += 'Each calls row gives unresolved target kind, expression, and line numbers by source index.\n'
            support += limitation_context(limitations)
            support += '\nAssess these uncertainties using the supplied source. Abstain if a judgment requires unavailable runtime targets.'
        prompt = messages(units, support)
        focused = [*prompt[:-1], {**prompt[-1], 'content': prompt[-1]['content']+'\nReviewer focus: '+max(FOCUS.values(), key=len)}]
        verification = verification_messages(prompt)
        # The verification envelope also bounds assessment, role focus, retry
        # instructions and both supported adapters' JSON wrappers.
        # A supplementary character takes twelve ASCII bytes in the transport
        # JSON, versus seven for an already escaped control in the inner JSON.
        wire_bound = len(json.dumps(verification).encode()) + 5 * 3 * 600 + 1024  # 1 KiB of transport metadata
        return {'units': units, 'support': support, 'input_tokens': count(focused),
                'support_tokens': count([{'content': support}]) if support else 0,
                'omitted_support': issues, 'required_evidence': required, 'analysis_limitations': limitations,
                'macro_expansions': expansions,
                'dependency_roots': sorted(roots),
                'prompt_sha256': digest(prompt), 'messages': prompt,
                'request_bytes_bound': wire_bound, 'verification_input_units': count(verification)}

    def combined(units):
        try:
            chunk = request(units)
        except SourceError as error:
            if str(error) != 'dependency closure exceeds analysis capacity':
                raise
            return None
        return chunk if within_capacity(chunk, limits) else None

    if strategy == 'adaptive' and len(pending) > 1:
        whole = combined(pending)
        if whole is not None:
            chunks.append(whole)
            pending = []

    while pending:
        group = [pending.pop(0)]
        chunk = request(group)
        if not within_capacity(chunk, limits):
            # A root's full dependencies must fit even before adding primary diff evidence.
            for root in chunk['dependency_roots']:
                if root not in source_fits:
                    source_fits[root] = within_capacity(request([], {root}), limits)
            if not all(source_fits[root] for root in chunk['dependency_roots']):
                chunk['omitted_support'] += ['required semantic closure exceeds request capacity']
            elif subdivisions >= ASSESSMENT_LIMIT:
                chunk['omitted_support'] += ['planning subdivision limit exceeded']
            else:
                try:
                    children = group[0].split()
                except ValueError:
                    chunk['omitted_support'] += ['indivisible evidence exceeds request capacity']
                else:
                    subdivisions += 1
                    pending[:0] = children
                    continue
        if within_capacity(chunk, limits) and pending:
            high = len(pending)
            if strategy == 'baseline':
                high = next((i for i, u in enumerate(pending) if u.path != group[0].path), high)
            low = 0
            while low < high:
                middle = (low + high + 1) // 2
                trial = combined(group + pending[:middle])
                if trial is None:
                    high = middle - 1
                else:
                    low, chunk = middle, trial
            del pending[:low]
        chunks.append(chunk)
    frozen = {'schema': SCHEMA, 'strategy': strategy, 'diff_digest': digest(diff), 'capacities': list(limits),
              'snapshots': {s: v.revision for s, v in sources.items()}, 'analyzers': analyzer_versions(),
            'analysis_limits': ['syntax-based conservative dependencies; bounded external macro dependency expansion; no runtime dispatch',
                                  'unsupported formats receive exact textual evidence only'],
              'primary_evidence': [a['id'] for a in anchors(original)],
              'nodes': {n: {**asdict(v), 'names': sorted(v.names), 'refs': sorted(v.refs)} for n, v in graph.nodes.items()},
              'edges': graph.edges,
              'external_imports': sorted(graph.external_imports),
              'chunks': [{**c, 'units': [asdict(u) for u in c['units']]} for c in chunks]}
    frozen['digest'] = digest(frozen)
    return {'strategy': strategy, 'original_units': len(original), 'primary_token_ceiling': limits[0],
            'chunks': chunks, 'review_plan': frozen}


def restore(frozen, diff, sources):
    limits = capacities()
    if (frozen.get('schema') != SCHEMA or frozen.get('digest') != digest({k: v for k, v in frozen.items() if k != 'digest'})
            or frozen.get('capacities') != list(limits) or frozen['diff_digest'] != digest(diff) or frozen['analyzers'] != analyzer_versions()
            or frozen['snapshots'] != {s: v.revision for s, v in sources.items()}):
        raise PlanningError('frozen evidence plan changed')
    chunks = [{**c, 'units': [Unit(**{**u, 'rows': tuple(u['rows'])}) for u in c['units']]} for c in frozen['chunks']]
    expected = Counter(a['id'] for a in anchors(parse(diff)))
    actual = Counter(a['id'] for c in chunks for a in anchors(c['units']))
    expected_coordinates = Counter(json.dumps(a, sort_keys=True) for a in anchors(parse(diff)))
    actual_coordinates = Counter(json.dumps(a, sort_keys=True) for c in chunks for a in anchors(c['units']))
    if expected_coordinates != actual_coordinates:
        raise PlanningError('source coordinates changed')
    if expected != actual or any(n != 1 for n in actual.values()) or list(expected.elements()) != frozen['primary_evidence']:
        raise PlanningError('primary evidence ownership changed')
    for c in chunks:
        if c['omitted_support'] or not within_capacity(c, limits):
            raise PlanningError('frozen evidence plan is incomplete')
        required = set(c['required_evidence'])
        if not set(c['dependency_roots']) <= required or not required <= frozen['nodes'].keys():
            raise PlanningError('required evidence is missing')
        if not all(set(frozen['edges'].get(n, [])) <= required for n in required):
            raise PlanningError('dependency closure is incomplete')
        if digest(c['messages']) != c['prompt_sha256']:
            raise PlanningError('frozen request changed')
        expected_limitations = [item for n in c['required_evidence']
                                for item in frozen['nodes'][n]['limitations']]
        if c.get('analysis_limitations') != expected_limitations:
            raise PlanningError('analysis limitations changed')
        expected_expansions = [item for n in c['required_evidence']
                               for item in frozen['nodes'][n].get('macro_expansions', [])]
        if c.get('macro_expansions') != expected_expansions:
            raise PlanningError('macro expansions changed')
        if expected_expansions and macro_context(expected_expansions) not in c['support']:
            raise PlanningError('macro expansions omitted from context')
        if expected_limitations and limitation_context(expected_limitations) not in c['support']:
            raise PlanningError('analysis limitations omitted from context')
    return {'strategy': frozen['strategy'], 'original_units': len(parse(diff)), 'primary_token_ceiling': limits[0],
            'chunks': chunks, 'review_plan': frozen}


def feasible(manifest):
    assessment, verification = capacities()
    return bool(manifest.get('chunks')) and len(manifest['chunks']) <= ASSESSMENT_LIMIT and not any(
        c.get('omitted_support') or c['input_tokens'] > assessment
        or c.get('verification_input_units', 0) > verification
        or c.get('request_bytes_bound', 0) > MAX_REQUEST_BYTES for c in manifest['chunks'])


def admit(manifests, binding):
    """Validate the quoted artifacts before qualification can incur cost."""
    from adversarial_review import extract_git_diff, text_changes
    from source_graph import git_snapshots
    from quote import plan_digest
    require_feasible(manifests)
    for name, manifest in manifests.items():
        if plan_digest(manifest) != binding['plans'].get(name):
            raise PlanningError('quoted evidence plan changed: '+name)
    diff, _ = extract_git_diff(binding['base'], binding['head'])
    diff, omitted, _ = text_changes(diff)
    if omitted:
        raise PlanningError('unsupported binary evidence')
    sources = git_snapshots(binding['base'], binding['head'])
    for name, manifest in manifests.items():
        if feasible(manifest):
            if not manifest.get('review_plan'):
                raise PlanningError('execution requires an immutable review plan')
            restore(manifest['review_plan'], diff, sources)


def require_feasible(manifests):
    if not any(feasible(m) for m in manifests.values()):
        reasons = sorted({str(g) for m in manifests.values() for c in m.get('chunks', []) for g in c.get('omitted_support', [])})
        raise PlanningError('no feasible evidence plan: '+('; '.join(reasons[:10]) or 'assessment capacity exceeded'))
