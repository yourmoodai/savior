"""Trusted model capabilities and immutable per-run role selection."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).parent
ROLES = ('correctness', 'security', 'compatibility', 'elephant', 'verification')
active_role = ContextVar('model_role', default='elephant')
routing_override = ContextVar('qualification_routing', default=None)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load():
    catalog = json.loads((ROOT / 'models.json').read_text())
    routing = json.loads((ROOT / 'routing.json').read_text())
    if routing_override.get() is not None:
        routing = routing_override.get()
    validate(catalog, routing)
    return catalog, routing


def validate(catalog, routing):
    if catalog.get('schema') != 1 or routing.get('schema') != 1 or set(routing.get('roles', {})) != set(ROLES):
        raise ValueError('invalid model catalog or routing')
    for name, model in catalog['models'].items():
        if model['region'] != 'us-east-1' or model['adapter'] not in ('converse', 'chat_completions'):
            raise ValueError('unsupported model endpoint')
        if model.get('endpoint', 'runtime') not in ('runtime', 'mantle'):
            raise ValueError('unsupported model endpoint')
        if model.get('path', '/openai/v1/chat/completions') not in ('/openai/v1/chat/completions', '/v1/chat/completions'):
            raise ValueError('unsupported model path')
        if type(model.get('temperature_supported', True)) is not bool or model.get('output_parameter', 'max_completion_tokens') not in ('max_completion_tokens', 'max_tokens'):
            raise ValueError('unsupported inference parameters')
        response_format = model.get('response_format', 'json')
        if type(model.get('json_attempts', 1)) is not int or model.get('json_attempts', 1) not in (1, 2):
            raise ValueError('unsupported json attempts')
        if model.get('cache_read_pricing', 'unsupported') not in ('unsupported', 'standard_input_upper_bound'):
            raise ValueError('unsupported cache pricing')
        if model.get('instruction_role', 'system') not in ('system', 'developer'):
            raise ValueError('unsupported instruction role')
        if 'reasoning_effort' in model and (model['reasoning_effort'] not in ('low', 'medium', 'high') or model['adapter'] != 'chat_completions'):
            raise ValueError('unsupported reasoning effort')
        if response_format not in ('json', 'reasoning_prefix_json') or (
                response_format != 'json' and model['adapter'] != 'chat_completions'):
            raise ValueError('unsupported response format')
        if not re.fullmatch(r'[a-zA-Z0-9.:/-]+', model['model_id']):
            raise ValueError('invalid model id')
        if not model['resources'] or any(not re.fullmatch(r'arn:aws:bedrock:us-(?:east|west)-[12]:[0-9]*:(?:foundation-model|inference-profile)/[a-zA-Z0-9.:/-]+', r) for r in model['resources']):
            raise ValueError('exact US inference resources required')
        if any(type(model[k]) is not int or model[k] < 4096 for k in ('context_tokens', 'output_tokens')):
            raise ValueError('invalid model limits')
        price = model['pricing']
        if any(type(price[k]) is not int or price[k] <= 0 for k in ('input_nanodollars', 'output_nanodollars')) or not price.get('source') or not price.get('checked'):
            raise ValueError('unsupported_pricing')
    if any(name not in catalog['models'] for name in routing['roles'].values()):
        raise ValueError('unknown routed model')


def manifest():
    catalog, routing = load()
    return {'routing': routing, 'models': {n: catalog['models'][n] for n in sorted(set(routing['roles'].values()))}}


def selected(role=None):
    catalog, routing = load()
    return catalog['models'][routing['roles'][role or active_role.get()]]


def cost(model, inputs, outputs):
    p = model['pricing']
    return inputs * p['input_nanodollars'] + outputs * p['output_nanodollars']


@contextmanager
def select(role):
    if role not in ROLES:
        raise ValueError('unknown reviewer role')
    token = active_role.set(role)
    try:
        yield
    finally:
        active_role.reset(token)
