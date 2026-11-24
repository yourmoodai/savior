"""Seed shared accounting once from the historical artifacts restored by pilot.cjs."""
import os
from pathlib import Path
from botocore.session import get_session

from preflight import restore
from shared_ledger import LIMIT, object_args


def main():
    bucket = os.environ.pop('SAVIOR_BEDROCK_LEDGER_BUCKET')
    directory = Path('review-result')
    if (not bucket or (directory/'ledger.db').exists() or
            not any((directory/'previous-ledgers').glob('*.zip'))):
        raise ValueError('clean restore of historical spending required')
    session = get_session()
    policy = session.create_client('iam').get_role_policy(RoleName='savior-bedrock-review',
        PolicyName='retire-legacy-review-inference')['PolicyDocument']
    deny = {'Effect':'Deny','Action':'bedrock:InvokeModel','Resource':'*'}
    if deny not in policy['Statement']:
        raise ValueError('retire legacy model invocation before seeding the shared budget')
    ledger = restore()
    ledger.policy_budget()
    data = Path(ledger.path).read_bytes()
    if len(data) > LIMIT:
        raise ValueError('restored ledger exceeds shared capacity')
    session.create_client('s3',region_name='us-east-1').put_object(
        **object_args(bucket),Body=data,IfNoneMatch='*',Metadata={'repository':'1418413891'})
    print('shared spending ledger seeded from validated history')


if __name__ == '__main__':
    main()
