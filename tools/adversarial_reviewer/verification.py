"""Verification selects immutable candidates; model prose has no editing authority."""
import hashlib
import json

from evidence import validate as validate_evidence, repetitive

SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['decisions'],
          'properties': {'decisions': {'type': 'array', 'items': {
              'type': 'object', 'additionalProperties': False,
              'required': ['finding_id', 'verdict', 'rationale'], 'properties': {
                  'finding_id': {'type': 'string'},
                  'verdict': {'enum': ['retain', 'reject', 'abstain']},
                  'rationale': {'type': 'string', 'minLength': 10, 'maxLength': 1200}}}}}}


def candidates(findings, anchors):
    records = {}
    for finding in findings:
        location = finding.get('location')
        if type(location) is not int or not 1 <= location <= len(anchors):
            raise ValueError('invalid candidate location')
        identity = hashlib.sha256(json.dumps([finding, anchors[location-1]], sort_keys=True).encode()).hexdigest()
        records[identity] = {'finding_id': identity, **finding}
    return list(records.values())


def validate(response, findings, anchors):
    _, _, grounded = validate_evidence({'rationale': 'Validate original candidate evidence.',
                                      'abstention': '', 'findings': findings}, anchors)
    originals = {r['finding_id']: g for r, g in zip(
        [candidates([f], anchors)[0] for f in findings], grounded)}
    if not isinstance(response, dict) or set(response) != {'decisions'} or not isinstance(response['decisions'], list):
        raise ValueError('invalid verification decisions')
    seen, retained, reasons, incomplete = set(), [], [], False
    for decision in response['decisions']:
        if not isinstance(decision, dict) or set(decision) != {'finding_id', 'verdict', 'rationale'}:
            raise ValueError('invalid verification decision fields')
        identity, verdict, rationale = (decision[k] for k in ('finding_id', 'verdict', 'rationale'))
        if not isinstance(identity, str) or identity not in originals or identity in seen:
            raise ValueError('unknown or duplicate verification finding id')
        if verdict not in ('retain', 'reject', 'abstain'):
            raise ValueError('invalid verification verdict')
        if not isinstance(rationale, str) or not 10 <= len(rationale.strip()) <= 1200 or repetitive(rationale):
            raise ValueError('invalid verification rationale')
        seen.add(identity)
        reasons.append(identity+': '+verdict+': '+rationale.strip())
        if verdict == 'retain':
            retained.append(identity)
        incomplete |= verdict == 'abstain'
    if seen != originals.keys():
        raise ValueError('missing verification decisions')
    evidence = [value for identity, value in originals.items() if identity in retained]
    return ('COMMENT' if incomplete else 'REQUEST_CHANGES' if evidence else 'APPROVE',
            ('review incomplete: ' if incomplete else '')+'\n'.join(reasons), evidence)
