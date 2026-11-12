"""Offline packing candidates; no production execution or spending authority."""
import ast
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adversarial_reviewer'))
from adversarial_review import review_messages
from bedrock_client import count_tokens
from chunk_plan import anchors, manifest, parse, plan, related, render
from model_catalog import digest, manifest as routing_manifest
from quote import estimate

LIMIT = 48000


def messages(units, support):
    return review_messages(render(units), sorted({u.path for u in units}), '', anchors(units),
                           supporting_context='\nSupporting evidence (context only):\n'+support if support else '')


def semantic_boundaries(unit):
    """Only claim semantic boundaries when a whole added/deleted source parses."""
    if not unit.rows or len({r[0] for r in unit.rows}) != 1 or unit.rows[0][0] not in '+-':
        return []
    source = ''.join(r[1:] for r in unit.rows)
    try:
        if unit.path.endswith('.py'):
            tree = ast.parse(source)
            return sorted({min([n.lineno, *[d.lineno for d in getattr(n, 'decorator_list', [])]]) - 1
                           for n in tree.body} - {0})
        if unit.path.endswith('.json'):
            json.loads(source)
            # Record boundaries are recognized lexically, after validating the document.
            depth, quoted, escaped, boundaries = 0, False, False, []
            for index, line in enumerate(source.splitlines(True)):
                for char in line:
                    if quoted:
                        if escaped:
                            escaped = False
                        elif char == '\\':
                            escaped = True
                        elif char == '"':
                            quoted = False
                    elif char == '"':
                        quoted = True
                    elif char in '[{':
                        depth += 1
                    elif char in ']}':
                        depth -= 1
                if (depth == 1 or depth == 2 and line.rstrip().endswith('},')) and line.rstrip().endswith(','):
                    boundaries.append(index + 1)
            return boundaries
    except (SyntaxError, ValueError):
        pass
    return []


def cut(unit, point):
    left = unit.rows[:point]
    return [replace(unit, id=unit.id+'/0', rows=left),
            replace(unit, id=unit.id+'/1', rows=unit.rows[point:], start=unit.start+point,
                    old=unit.old+sum(r.startswith((' ', '-')) for r in left),
                    new=unit.new+sum(r.startswith((' ', '+')) for r in left))]


def candidate(diff):
    original = parse(diff)
    pending = [(u, []) for u in original]
    pieces = []
    while pending:
        unit, gaps = pending.pop(0)
        if count_tokens(messages([unit], '')) <= LIMIT-1024:
            pieces.append((unit, gaps))
            continue
        boundaries = semantic_boundaries(unit)
        if boundaries:
            children, cursor = [], unit
            previous = 0
            for point in boundaries:
                left, cursor = cut(cursor, point-previous)
                children.append(left)
                previous = point
            children.append(cursor)
            pending[:0] = [(u, gaps) for u in children]
        else:
            pending[:0] = [(u, sorted(set(gaps+[unit.id.split('/')[0]]))) for u in unit.split()]
    groups = []
    for unit, gaps in pieces:
        if not groups or count_tokens(messages(groups[-1][0]+[unit], '')) > LIMIT-1024:
            groups.append(([], set()))
        groups[-1][0].append(unit)
        groups[-1][1].update(gaps)
    chunks = []
    for units, gaps in groups:
        owners = {u.id.split('/')[0] for u in units}
        support, omitted = [], set(gaps)
        for other in original:
            if other.id in owners or not any(related(u, other) <= 1 for u in units):
                continue
            if len(render(support+[other]).encode()) <= 8000 and count_tokens(messages(units, render(support+[other]))) <= LIMIT-1024:
                support.append(other)
            else:
                omitted.add(other.id)
        marker = f'Context gaps: {len(omitted)} omitted or split units; manifest digest {digest(sorted(omitted))}.\n' if omitted else ''
        prompt = messages(units, marker+render(support))
        chunks.append({'units': units, 'support': marker+render(support), 'input_tokens': count_tokens(prompt),
                       'prompt_sha256': digest(prompt), 'support_tokens': count_tokens([{'content': render(support)}]) if support else 0,
                       'omitted_support': sorted(omitted), 'split_context': sorted(gaps)})
    return {'strategy': 'semantic_bytes', 'original_units': len(original),
            'primary_token_ceiling': LIMIT, 'chunks': chunks}


def compare(diff):
    expected = Counter(a['id'] for a in anchors(parse(diff)))
    results = {}
    for strategy in ('baseline', 'adaptive', 'semantic_bytes'):
        try:
            packed = candidate(diff) if strategy == 'semantic_bytes' else plan(diff, count_tokens, messages, strategy=strategy)
            result = manifest(packed)
            actual = Counter(e for c in result['chunks'] for e in c['evidence_ids'])
            result['coverage_complete'] = actual == expected and all(n == 1 for n in actual.values())
            result['context_gaps'] = [c['omitted_support'] for c in packed['chunks']]
            result['split_context'] = [c.get('split_context', []) for c in packed['chunks']]
            result['support_bytes'] = sum(len(c['support'].encode()) for c in packed['chunks'])
            result['prompt_bytes_with_allowance'] = sum(c['input_tokens'] for c in packed['chunks'])
            result['reservation_nanodollars'] = sum(i['reserved_nanodollars'] for i in estimate(result))
            results[strategy] = result
        except ValueError as error:
            results[strategy] = {'coverage_complete': False, 'error': str(error)}
    eligible = [s for s, p in results.items() if p['coverage_complete'] and len(p['chunks']) <= 512]
    selected = min(eligible, key=lambda s: (results[s]['reservation_nanodollars'], s)) if eligible else None
    sources = [Path(__file__), *[Path(__file__).parents[1]/'adversarial_reviewer'/name for name in
               ('chunk_plan.py', 'adversarial_review.py', 'bedrock_client.py', 'quote.py', 'model_catalog.py')]]
    binding = {'diff': digest(diff), 'routing': routing_manifest(), 'plans': results, 'selected': selected,
               'implementation': digest({p.name: p.read_text() for p in sources})}
    return {**binding, 'fingerprint': digest(binding), 'quality_qualified': False,
            'note': 'review reservations only; no qualification cost, paid calls, or quality-equivalence claim'}


def verify_binding(report, diff):
    if report != compare(diff):
        raise ValueError('packing replay binding mismatch')
