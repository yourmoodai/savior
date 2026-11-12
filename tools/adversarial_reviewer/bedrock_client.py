"""Bounded, tool-free Bedrock calls using short-lived AWS credentials."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from budget import Ledger, BudgetError, BudgetExhausted
from diagnostics import record
from prompt_profiles import settings
from model_catalog import selected, cost as model_cost

MAX_RESPONSE_BYTES = 1048576  # 1 MiB
MAX_REQUEST_BYTES = 524288  # 512 KiB


class BedrockError(Exception):
    """Content-free diagnostic safe to publish in a review report."""


def conversation(messages):
    return {'system': [{'text': m['content']} for m in messages if m['role'] == 'system'],
            'messages': [{'role': m['role'], 'content': [{'text': m['content']}]} for m in messages if m['role'] != 'system']}


def count_tokens(messages, deadline=None):
    """Estimate packing size without claiming an exact model token count."""
    # DeepSeek V3.2 has no CountTokens API. Spending reserves full context separately.
    return 1024 + sum(128 + len(m['content'].encode('utf-8')) for m in messages)


def invoke(operation, payload, deadline):
    remaining = min(240, deadline - time.monotonic())
    if remaining <= 0:
        raise BedrockError('bedrock assessment deadline exhausted')
    region = 'us-east-1'
    request = json.dumps(payload)
    if len(request.encode()) > MAX_REQUEST_BYTES:
        raise BedrockError('bedrock request exceeds size limit')
    # A private temporary file keeps review evidence out of process arguments.
    env = {key: value for key, value in os.environ.items() if key.startswith('AWS_') or key in ('PATH', 'HOME')}
    env.update(AWS_MAX_ATTEMPTS='1', AWS_PAGER='')
    try:
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.json') as payload:
            payload.write(request)
            payload.flush()
            result = subprocess.run(['aws', 'bedrock-runtime', operation, '--region', region,
                                     '--cli-input-json', 'file://'+payload.name, '--output', 'json',
                                     '--cli-connect-timeout', '10', '--cli-read-timeout', str(max(1, int(remaining)))],
                                    text=True, capture_output=True, env=env, timeout=remaining, check=False)
    except subprocess.TimeoutExpired:
        raise BedrockError('bedrock call exceeded its execution deadline') from None
    except OSError:
        raise BedrockError('aws cli unavailable') from None
    if result.returncode:
        raise BedrockError('bedrock invocation failed; check role permissions, model access and quota')
    if len(result.stdout.encode()) > MAX_RESPONSE_BYTES:
        raise BedrockError('bedrock response exceeds size limit')
    try:
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise BedrockError('bedrock returned invalid JSON') from None


def complete(messages, deadline, *, purpose='assessment', identity=None, expanded=False, transport_attempt=0, json_attempt=0):
    if os.environ.get('BEDROCK_REVIEW_ENABLED') != 'true':
        raise BedrockError('bedrock review disabled')
    try:
        model = selected()
    except (ValueError, KeyError):
        raise BedrockError('invalid model routing') from None
    if os.environ.get('AWS_REGION') != model['region']:
        raise BedrockError('bedrock region is not configured for this reviewer')
    policy = os.environ.get('REVIEW_COST_POLICY') == 'true'
    if os.environ.get('GITHUB_ACTIONS') == 'true' and not policy:
        raise BedrockError('repository cost policy required')
    if not policy and not os.environ.get('REVIEW_BUDGET_GRANT'):
        raise BedrockError('explicit spending approval required')
    if policy:
        from model_catalog import current_pricing
        try:
            current_pricing(model)
        except ValueError:
            raise BedrockError('unsupported_pricing') from None
    profile, temperature, before_call = settings.get()
    payload = {'modelId': model['model_id'], **conversation(messages),
               'inferenceConfig': {'maxTokens': (4096 if expanded else {'overview': 256, 'verification': 1536}.get(purpose, 1024)), 'temperature': temperature}}
    if not model.get('temperature_supported', True):
        payload['inferenceConfig'].pop('temperature')
    if len(json.dumps(payload).encode()) > MAX_REQUEST_BYTES:
        raise BedrockError('bedrock request exceeds size limit')
    if deadline <= time.monotonic():
        raise BedrockError('bedrock assessment deadline exhausted')
    path = os.environ.get('REVIEW_BUDGET_LEDGER')
    if not path:
        raise BedrockError('persistent review budget ledger required')
    inputs = count_tokens(messages, deadline)
    if inputs > min(48000, model['context_tokens'] - 4096):
        raise BedrockError('prompt exceeds token budget; subdivision required')
    ledger = Ledger(path)
    identity = identity or hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()
    if before_call is not None:
        before_call(model_cost(model, model['context_tokens'], payload['inferenceConfig']['maxTokens']))
    try:
        key = ledger.reserve(model['context_tokens'], payload['inferenceConfig']['maxTokens'],
                             os.environ.get('REVIEW_BUDGET_PHASE', 'review'), purpose+':'+identity, model=model)
        started = time.monotonic()
        if model['adapter'] == 'converse':
            envelope = invoke('converse', payload, deadline)
        else:
            from chat_transport import invoke_chat, ChatError
            try:
                envelope = invoke_chat(model, messages, payload['inferenceConfig'], deadline)
            except ChatError as error:
                if error.envelope is not None:
                    ledger.reconcile(key, error.envelope, time.monotonic()-started)
                if error.retryable and transport_attempt < 2 and deadline-time.monotonic() > 2**transport_attempt:
                    time.sleep(2**transport_attempt)
                    return complete(messages, deadline, purpose=purpose, identity=identity,
                                    expanded=expanded, transport_attempt=transport_attempt+1, json_attempt=json_attempt)
                raise
        record(stage='transport', profile=profile, purpose=purpose, request_hash=identity,
               inference_config=payload['inferenceConfig'], response=envelope, seconds=time.monotonic()-started)
        ledger.reconcile(key, envelope, time.monotonic()-started)
        from result_cache import measure
        measure('call', input_tokens=envelope['usage']['inputTokens'], output_tokens=envelope['usage']['outputTokens'])
    except BudgetError as error:
        (Path(path).parent / 'budget-blocked.json').write_text(json.dumps({
            'reason': 'budget_exhausted' if isinstance(error, BudgetExhausted) else 'spending_unresolved'}))
        raise BedrockError(str(error)) from None
    try:
        if envelope['usage']['inputTokens'] > 48000:
            raise BedrockError('actual input exceeds token budget; subdivision required')
        if envelope.get('stopReason') == 'max_tokens' and not expanded and purpose != 'overview':
            return complete(messages, deadline, purpose=purpose, identity=identity, expanded=True, transport_attempt=transport_attempt, json_attempt=json_attempt)
        if envelope.get('stopReason') == 'max_tokens':
            raise BedrockError('response incomplete: subdivision required')
        if envelope.get('stopReason') != 'end_turn':
            raise BedrockError('bedrock response incomplete or blocked')
        content = envelope['output']['message']['content']
        if not content or any(set(block) != {'text'} for block in content):
            raise ValueError()
        raw = ''.join(block['text'] for block in content).strip()
        if model['adapter'] == 'chat_completions':
            from chat_transport import answer_text
            # Usage includes reasoning, even when answer extraction fails.
            raw = answer_text(raw, model.get('response_format', 'json'))
        fenced = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```', raw, re.DOTALL)
        value = json.loads(fenced.group(1) if fenced else raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, KeyError, TypeError, AttributeError):
        record(stage='invalid_answer', purpose=purpose, attempt=json_attempt)
        if json_attempt+1 < model.get('json_attempts', 1) and deadline > time.monotonic():
            retry = [*messages, {'role': 'system', 'content':
                'The previous response was invalid JSON. Assess the original evidence again. '
                'Return exactly one complete JSON object matching the requested fields. '
                'Finish reasoning before writing the final JSON; do not include unfinished hypotheses.'}]
            return complete(retry, deadline, purpose=purpose, expanded=expanded,
                            transport_attempt=transport_attempt, json_attempt=json_attempt+1)
        raise BedrockError('bedrock returned invalid JSON') from None
