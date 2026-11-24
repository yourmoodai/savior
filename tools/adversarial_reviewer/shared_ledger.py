"""Publish a committed SQLite ledger before returning authority to invoke Bedrock."""
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from botocore.exceptions import BotoCoreError, ClientError
from botocore.session import get_session

ACCOUNT = '463509296941'
KEY = 'savior/bedrock/ledger.db'
LIMIT = 10_000_000  # 10 MB, matching historical artifact admission


def object_args(bucket):
    return {'Bucket': bucket, 'Key': KEY, 'ExpectedBucketOwner': ACCOUNT}


@contextmanager
def shared_snapshot(bucket, client=None):
    from budget import BudgetError
    args = object_args(bucket)
    try:
        client = client or get_session().create_client('s3', region_name='us-east-1')
        obj = client.get_object(**args)
        with closing(obj['Body']) as stream:
            data = stream.read(LIMIT + 1)
        if (len(data) != obj['ContentLength'] or len(data) > LIMIT or
                not data.startswith(b'SQLite format 3\x00') or obj['Metadata'].get('repository') != '1418413891'):
            raise BudgetError('invalid shared spending ledger')
        with TemporaryDirectory() as temp:
            path = Path(temp)/'ledger.db'
            path.write_bytes(data)
            yield path
            updated = path.read_bytes()
            if updated != data:
                if len(updated) > LIMIT:
                    raise BudgetError('shared ledger exceeds capacity')
                client.put_object(**args, Body=updated, IfMatch=obj['ETag'], Metadata=obj['Metadata'])
    except (ClientError, BotoCoreError) as error:
        # An ambiguous write may have succeeded. Keep the reservation and stop this dispatch.
        raise BudgetError('shared ledger unavailable or concurrently modified') from error
