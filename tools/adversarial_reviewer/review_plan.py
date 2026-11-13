"""One immutable evidence plan for offline admission and subsequent execution."""
from collections import Counter
from contextvars import ContextVar
from dataclasses import asdict
import json

from chunk_plan import Unit, anchors, parse, ASSESSMENT_LIMIT
from source_graph import Graph, analyzer_versions, digest

SCHEMA = 2
frozen_execution = ContextVar('frozen_review_plan', default=None)


class PlanningError(ValueError):
    pass


def verification_bound(messages):
    from adversarial_review import VERIFICATION_PROMPT
    from prompt_profiles import prompts
    from egm import FOCUS
    from bedrock_client import estimate_input_units
    from verification import SCHEMA as verification_schema
    finding = {'finding_id': 'f'*64, 'location': 999999999, **{k: '\U0001f9ea'*600 for k in ('invariant', 'scenario', 'correction')}}
    expanded = [dict(m) for m in messages]
    expanded[-1]['content'] += '\n'+prompts('', VERIFICATION_PROMPT)[1]+'\nCandidate findings to verify: '+json.dumps([finding])+'\n'
    expanded[-1]['content'] += '\nReviewer focus: '+max(FOCUS.values(), key=len)
    expanded[-1]['content'] += json.dumps(verification_schema)
    return estimate_input_units(expanded)


def limitation_context(items):
    return json.dumps([[i['side'], i['path'], i['line'], i['expression'],
                        'callback' if i['reason'].startswith('injected') else 'receiver']
                       for i in items], separators=(',', ':'))


def pack(diff, count, messages, sources, strategy):
    graph = Graph(sources)
    original = parse(diff)
    chunks, pending = [], sorted(original, key=lambda u: (u.path, u.start))
    source_fits, subdivisions = {}, 0

    def request(units, roots=None):
        roots = {n for u in units for n in graph.roots(u)} if roots is None else roots
        required, issues = graph.closure(roots)
        support = '\n'.join(f'[{graph.nodes[n].side} {graph.nodes[n].path}:{graph.nodes[n].start}-{graph.nodes[n].end}]\n'
                            +graph.nodes[n].text for n in required)
        limitations = [item for n in required for item in graph.nodes[n].limitations]
        if limitations:
            support += '\nRuntime analysis limitations (source coverage is not runtime proof):\n'
            support += 'Each row: revision side, path, line, expression, unresolved target kind.\n'
            support += limitation_context(limitations)
            support += '\nAssess these uncertainties using the supplied source. Abstain if a judgment requires unavailable runtime targets.'
        prompt = messages(units, support)
        return {'units': units, 'support': support, 'input_tokens': count(prompt),
                'support_tokens': count([{'content': support}]) if support else 0,
                'omitted_support': issues, 'required_evidence': required, 'analysis_limitations': limitations,
                'dependency_roots': sorted(roots),
                'prompt_sha256': digest(prompt), 'messages': prompt,
                'verification_input_units': verification_bound(prompt)}

    def fits(chunk):
        return chunk['input_tokens'] <= 48000 and chunk['verification_input_units'] <= 48000

    while pending:
        group = [pending.pop(0)]
        chunk = request(group)
        if not fits(chunk):
            # A root's full dependencies must fit even before adding primary diff evidence.
            for root in chunk['dependency_roots']:
                if root not in source_fits:
                    source_fits[root] = fits(request([], {root}))
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
        if fits(chunk):
            while pending:
                other = pending[0]
                if strategy == 'baseline' and other.path != group[0].path:
                    break
                trial = request(group+[other])
                if not fits(trial) or len(group) >= 16 or len({u.path for u in group+[other]}) > 8:
                    break
                group.append(pending.pop(0))
                chunk = trial
        chunks.append(chunk)
    frozen = {'schema': SCHEMA, 'strategy': strategy, 'diff_digest': digest(diff),
              'snapshots': {s: v.revision for s, v in sources.items()}, 'analyzers': analyzer_versions(),
              'analysis_limits': ['syntax-based conservative dependencies; no runtime dispatch or macro expansion',
                                  'unsupported formats receive exact textual evidence only'],
              'primary_evidence': [a['id'] for a in anchors(original)],
              'nodes': {n: {**asdict(v), 'names': sorted(v.names), 'refs': sorted(v.refs)} for n, v in graph.nodes.items()},
              'edges': graph.edges,
              'external_imports': sorted(graph.external_imports),
              'chunks': [{**c, 'units': [asdict(u) for u in c['units']]} for c in chunks]}
    frozen['digest'] = digest(frozen)
    return {'strategy': strategy, 'original_units': len(original), 'primary_token_ceiling': 48000,
            'chunks': chunks, 'review_plan': frozen}


def restore(frozen, diff, sources):
    if (frozen.get('schema') != SCHEMA or frozen.get('digest') != digest({k: v for k, v in frozen.items() if k != 'digest'})
            or frozen['diff_digest'] != digest(diff) or frozen['analyzers'] != analyzer_versions()
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
        if c['omitted_support'] or c['verification_input_units'] > 48000 or c['input_tokens'] > 48000:
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
        if expected_limitations and limitation_context(expected_limitations) not in c['support']:
            raise PlanningError('analysis limitations omitted from context')
    return {'strategy': frozen['strategy'], 'original_units': len(parse(diff)), 'primary_token_ceiling': 48000,
            'chunks': chunks, 'review_plan': frozen}


def feasible(manifest):
    return bool(manifest.get('chunks')) and len(manifest['chunks']) <= ASSESSMENT_LIMIT and not any(
        c.get('omitted_support') or c.get('input_tokens', 0) > 48000
        or c.get('verification_input_units', 0) > 48000 for c in manifest['chunks'])


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
