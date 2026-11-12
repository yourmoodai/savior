"""Tool-free AWS Chat Completions with SigV4 and no provider-key fallback."""
import json
import time
import re
from bedrock_client import BedrockError
from diagnostics import record
from model_catalog import active_role


def answer_text(raw, response_format):
    """Remove only an explicitly configured, complete leading reasoning block."""
    if response_format == 'json':
        return raw
    if response_format != 'reasoning_prefix_json':
        raise ValueError('unsupported response format')
    opening, closing = '<reasoning>', '</reasoning>'
    if not raw.startswith(opening):
        return raw
    reasoning, delimiter, answer = raw[len(opening):].partition(closing)
    if not delimiter or opening in reasoning or opening in answer or closing in answer or not answer.strip():
        raise ValueError('invalid reasoning wrapper')
    return answer.strip()


class ChatError(BedrockError):
    def __init__(self, category, *, retryable=False, envelope=None):
        super().__init__('bedrock chat '+category)
        self.category = category
        self.retryable = retryable
        self.envelope = envelope


def normalize(value):
    envelope = None
    category = 'invalid_envelope'
    try:
        usage = value['usage']
        inputs, outputs = usage['prompt_tokens'], usage['completion_tokens']
        if any(type(n) is not int or n < 0 for n in (inputs, outputs)):
            raise ChatError('invalid_usage')
        cached = usage.get('prompt_tokens_details', {}).get('cached_tokens', 0)
        if type(cached) is not int or not 0 <= cached <= inputs:
            raise ChatError('invalid_cached_usage')
        envelope = {'usage': {'inputTokens': inputs, 'outputTokens': outputs}}
        if cached:
            envelope['usage']['cacheReadInputTokens'] = cached
        if len(value['choices']) != 1:
            raise ValueError()
        choice = value['choices'][0]
        message = choice['message']
        if message.get('refusal') or message.get('tool_calls') or message.get('function_call'):
            raise ChatError('refusal_or_tools', envelope=envelope)
        if not isinstance(message['content'], str):
            raise ChatError('invalid_content', envelope=envelope)
        return {**envelope,
                'stopReason': {'stop': 'end_turn', 'length': 'max_tokens'}.get(choice['finish_reason'], 'blocked'),
                'output': {'message': {'content': [{'text': message['content']}]}}}
    except ChatError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ChatError(category, envelope=envelope) from None


def invoke_chat(model, messages, config, deadline):
    from bedrock_client import BedrockError, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES
    # Botocore supplies credential resolution and signing; retries remain owned by
    # the approved review plan, not by an SDK default.
    try:
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        from botocore.httpsession import URLLib3Session
        from botocore.session import Session
    except ImportError:
        raise BedrockError('chat signing dependency unavailable') from None
    remaining = min(240, deadline - time.monotonic())
    if remaining <= 0:
        raise BedrockError('bedrock assessment deadline exhausted')
    endpoint = model.get('endpoint', 'runtime')
    service = 'bedrock' if endpoint == 'runtime' else 'bedrock-mantle'
    host = (f"bedrock-runtime.{model['region']}.amazonaws.com" if endpoint == 'runtime'
            else f"bedrock-mantle.{model['region']}.api.aws")
    url = 'https://' + host + model.get('path', '/openai/v1/chat/completions')
    messages = [{**m, 'role': model.get('instruction_role', 'system') if m['role'] == 'system' else m['role']} for m in messages]
    payload = {'model': model['model_id'], 'messages': messages, 'stream': False,
               model.get('output_parameter', 'max_completion_tokens'): config['maxTokens']}
    if model.get('temperature_supported', True):
        payload['temperature'] = config['temperature']
    if model.get('reasoning_effort'):
        payload['reasoning_effort'] = model['reasoning_effort']
    body = json.dumps(payload)
    if len(body.encode()) > MAX_REQUEST_BYTES:
        raise BedrockError('bedrock request exceeds size limit')
    started = time.monotonic()
    metadata = {'stage': 'chat_transport', 'model': model['model_id'], 'role': active_role.get()}
    try:
        credentials = Session().get_credentials().get_frozen_credentials()
        request = AWSRequest(method='POST', url=url, data=body, headers={'Content-Type': 'application/json'})
        SigV4Auth(credentials, service, model['region']).add_auth(request)
        session = URLLib3Session(timeout=remaining)
        try:
            response = session.send(request.prepare())
            metadata['http_status'] = response.status_code
            headers = {k.lower(): v for k, v in response.headers.items()}
            request_id = headers.get('x-amzn-requestid', '')
            if re.fullmatch(r'[a-fA-F0-9-]{16,64}', request_id):
                metadata['request_id'] = request_id
            code = headers.get('x-amzn-errortype', '').split(':')[0]
            if code in ('ThrottlingException', 'AccessDeniedException', 'ValidationException',
                        'InternalServerException', 'ServiceUnavailableException', 'ModelTimeoutException'):
                metadata['provider_code'] = code
            if response.status_code != 200:
                raise ChatError('http_error', retryable=response.status_code in (429, 500, 502, 503, 504))
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise ChatError('response_too_large')
            try:
                value = json.loads(response.content)
            except ValueError:
                raise ChatError('invalid_json_envelope') from None
            # Captured only inside the synthetic qualification diagnostic context.
            record(stage='chat_response', model=model['model_id'], role=active_role.get(), response=value)
            return normalize(value)
        finally:
            session.close()
    except ChatError as error:
        record(**metadata, category=error.category, seconds=time.monotonic()-started)
        raise
    except Exception as error:
        from botocore.exceptions import ConnectTimeoutError, ReadTimeoutError, ConnectionClosedError, EndpointConnectionError
        transient = isinstance(error, (ConnectTimeoutError, ReadTimeoutError, ConnectionClosedError, EndpointConnectionError))
        category = 'network_error' if transient else 'client_error'
        record(**metadata, category=category, exception_type=type(error).__name__, seconds=time.monotonic()-started)
        raise ChatError(category, retryable=transient) from None
