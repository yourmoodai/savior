"""Discover without inference; apply only explicit, revision-bound onboarding plans."""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import tracemalloc
import sys

from model_catalog import ROOT, ROLES, digest, load, manifest, routing_override


def aws(service, operation, **payload):
    if not os.environ.get('AWS_REGION') or not (os.environ.get('AWS_PROFILE') or os.environ.get('AWS_ACCESS_KEY_ID')):
        raise ValueError('configured aws profile or workload credentials and region required')
    with tempfile.NamedTemporaryFile(mode='w+') as request:
        json.dump(payload, request); request.flush()
        result = subprocess.run(['aws', service, operation, '--region', os.environ['AWS_REGION'],
                                 '--cli-input-json', 'file://'+request.name, '--output', 'json'],
                                capture_output=True, text=True, timeout=60,
                                env={**os.environ, 'AWS_PAGER': '', 'AWS_MAX_ATTEMPTS': '1'})
    if result.returncode:
        raise ValueError('aws operation failed: '+operation)
    return json.loads(result.stdout or '{}')


def account():
    trust = json.loads((ROOT/'iam/trust.json').read_text())
    arn = trust['Statement'][0]['Principal']['Federated']
    return arn.split(':')[4]


def check_account():
    if os.environ.get('AWS_REGION') != 'us-east-1':
        raise ValueError('us-east-1 entrypoint required')
    if aws('sts', 'get-caller-identity')['Account'] != account():
        raise ValueError('aws account differs from configured trust boundary')


def accessible(value):
    return (value.get('agreementAvailability', {}).get('status') == 'AVAILABLE'
            and value.get('authorizationStatus') == 'AUTHORIZED'
            and value.get('entitlementAvailability') == 'AVAILABLE'
            and value.get('regionAvailability') == 'AVAILABLE')


def discover():
    check_account()
    result = {'schema': 1, 'account': account(), 'region': 'us-east-1', 'discovered_at': int(time.time()),
              'models': aws('bedrock', 'list-foundation-models')['modelSummaries'],
              'profiles': aws('bedrock', 'list-inference-profiles').get('inferenceProfileSummaries', [])}
    for model in result['models']:
        model['state'] = 'discovered'
        try:
            model['availability'] = aws('bedrock', 'get-foundation-model-availability', modelId=model['modelId'])
            if accessible(model['availability']):
                model['state'] = 'accessible'
            model['offers'] = aws('bedrock', 'list-foundation-model-agreement-offers', modelId=model['modelId']).get('offers', [])
        except ValueError:
            model['access_check'] = 'unavailable; no runtime probe attempted'
    return result


def plan(inventory, names, use_case=None):
    from quote import configuration, qualification_estimate
    catalog, _ = load()
    if inventory['account'] != account() or inventory['region'] != 'us-east-1' or not 0 <= time.time()-inventory['discovered_at'] < 86400:
        raise ValueError('inventory stale or outside account boundary')
    selected = {name: catalog['models'][name] for name in names}
    agreements = []
    for model in selected.values():
        if model.get('endpoint', 'runtime') != 'runtime':
            raise ValueError('mantle onboarding requires separately reviewed resource-scoped iam policy')
        resources = set(model['resources'])
        if model['model_id'].startswith('us.'):
            profile = next(p for p in inventory['profiles'] if p['inferenceProfileId'] == model['model_id'])
            required = {profile['inferenceProfileArn'], *(m['modelArn'] for m in profile['models'])}
            if resources != required:
                raise ValueError('profile resources differ from discovery')
            ids = {r.split('/')[1] for r in required if ':foundation-model/' in r}
        else:
            ids = {model['model_id']}
            if resources != {f"arn:aws:bedrock:us-east-1::foundation-model/{model['model_id']}"}:
                raise ValueError('model resources differ from invocation target')
        for identity in ids:
            found = next(m for m in inventory['models'] if m['modelId'] == identity)
            if not accessible(found.get('availability', {})):
                offers = found.get('offers', [])
                if len(offers) != 1:
                    raise ValueError('select exactly one provider offer in reviewed inventory')
                agreements.append({'model_id': identity, 'offer': offers[0]})
    policy = {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Action': 'bedrock:InvokeModel',
               'Resource': sorted({r for m in selected.values() for r in m['resources']})}]}
    now = int(time.time())
    value = {'schema': 1, 'account': account(), 'region': 'us-east-1', 'issued_at': now,
             'expires_at': now+86400, 'configuration': configuration(), 'models': selected,
             'policy': policy, 'agreements': agreements, 'use_case': use_case,
             'qualification': qualification_estimate(), 'routing': manifest(),
             'notice': 'activation accepts listed provider terms; qualification needs separate spending approval'}
    value['id'] = digest(value)
    return value


def validate_plan(value, approval):
    from quote import configuration
    if (approval != value.get('id') or approval != digest({k: v for k, v in value.items() if k != 'id'})
            or value['configuration'] != configuration() or value['account'] != account()
            or value['region'] != 'us-east-1' or not value['issued_at'] <= time.time() < value['expires_at']):
        raise ValueError('approval missing, stale or changed')


def activate(value, approval):
    validate_plan(value, approval)
    check_account()
    if value['use_case']:
        aws('bedrock', 'put-use-case-for-model-access',
            formData=base64.b64encode(json.dumps(value['use_case']).encode()).decode())
    for agreement in value['agreements']:
        current = aws('bedrock', 'get-foundation-model-availability', modelId=agreement['model_id'])
        if current.get('agreementAvailability', {}).get('status') not in ('AVAILABLE', 'PENDING'):
            offers = aws('bedrock', 'list-foundation-model-agreement-offers', modelId=agreement['model_id']).get('offers', [])
            if agreement['offer'] not in offers:
                raise ValueError('provider offer changed; regenerate plan')
            aws('bedrock', 'create-foundation-model-agreement', modelId=agreement['model_id'],
                offerToken=agreement['offer']['offerToken'])
    # This named policy is owned by this tool; unrelated role policies survive.
    aws('iam', 'put-role-policy', RoleName='savior-bedrock-review', PolicyName='savior-review-models',
        PolicyDocument=json.dumps(value['policy']))
    return {'activated': sorted(value['models']), 'qualified': False, 'review_enabled': False}


def qualification_quote():
    from quote import configuration, qualification_estimate
    estimate = qualification_estimate()
    now = int(time.time())
    value = {'schema': 1, 'account': account(), 'region': 'us-east-1', 'configuration': configuration(),
             'routing': manifest(), 'issued_at': now, 'expires_at': now+86400,
             'cap_nanodollars': estimate['reserved_nanodollars'], 'estimate': estimate}
    value['id'] = digest(value)
    return value


def qualify(value, approval, directory):
    token = routing_override.set(value['routing']['routing'])
    try:
        return _qualify(value, approval, directory)
    finally:
        routing_override.reset(token)


def _qualify(value, approval, directory):
    validate_plan(value, approval)
    if value['routing'] != manifest():
        raise ValueError('qualification routing changed')
    check_account()
    from budget import Ledger
    from qualify import qualify as run
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    os.environ.update(REVIEW_BUDGET_LEDGER=str(directory/'ledger.db'), REVIEW_BUDGET_GRANT=value['id'],
                      REVIEW_RESULT_CACHE=str(directory/'cache'), BEDROCK_REVIEW_ENABLED='true')
    Ledger(os.environ['REVIEW_BUDGET_LEDGER']).authorize({'id': value['id'], 'cap_nanodollars': value['cap_nanodollars'],
                                                       'quote': value})
    return run(directory/'qualification.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['discover', 'plan', 'activate', 'quote', 'qualify', 'status'])
    parser.add_argument('--input', type=Path)
    parser.add_argument('--models', nargs='+')
    parser.add_argument('--candidate', help='qualify one catalog model in all roles without promoting it')
    parser.add_argument('--use-case', type=Path)
    parser.add_argument('--approve')
    parser.add_argument('--directory', type=Path)
    args = parser.parse_args()
    try:
        value = json.loads(args.input.read_text()) if args.input else None
        if args.command == 'discover':
            result = discover()
        elif args.command == 'plan':
            result = plan(value, args.models or sorted(load()[0]['models']),
                          json.loads(args.use_case.read_text()) if args.use_case else None)
        elif args.command == 'activate':
            result = activate(value, args.approve)
        elif args.command == 'quote':
            if args.candidate:
                routing_override.set({'schema': 1, 'roles': {role: args.candidate for role in ROLES}})
            result = qualification_quote()
        elif args.command == 'qualify':
            if not args.directory:
                raise ValueError('persistent qualification directory required')
            result = qualify(value, args.approve, args.directory)
        else:
            from qualify import cached_qualification
            if args.directory:
                os.environ['REVIEW_RESULT_CACHE'] = str(args.directory/'cache')
            _, qualification = cached_qualification()
            result = {'selected': manifest(), 'qualified': bool(qualification), 'qualification': qualification}
        print(json.dumps(result, indent=2))
        if tracemalloc.is_tracing():
            print(json.dumps({'peak_allocated_bytes': tracemalloc.get_traced_memory()[1]}), file=sys.stderr)
    except (ValueError, KeyError, TypeError, StopIteration, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, 'model operation failed: '+str(error)+'\n')


if __name__ == '__main__':
    main()
