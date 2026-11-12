"""Append-only research spending history, independent of production allowances."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import time
import uuid

from model_catalog import active_role, cost, digest

EXPERIMENT = 'review-designs-v1'
CAP = 4_000_000_000  # nanodollars: $4 across all research calls


class ResearchStopped(Exception):
    pass


def durable_json(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x') as output:
        json.dump(value, output, sort_keys=True, indent=2)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class ResearchLedger:
    def __init__(self, root):
        self.root = Path(root)
        self.anchor = self.root.with_suffix('.anchor.json')
        self.path = self.root / 'ledger.jsonl'
        self.context = {}
        self.events()

    @classmethod
    def initialize(cls, root, frozen):
        root = Path(root)
        root.parent.mkdir(parents=True, exist_ok=True)
        anchor = root.with_suffix('.anchor.json')
        if root.exists() or anchor.exists():
            raise ResearchStopped('research history already exists; resume without initialization')
        # The external anchor survives losing the experiment directory. An interrupted
        # initialization also requires recovery, never a new spending allowance.
        with anchor.open('x') as output:
            json.dump({'experiment': EXPERIMENT, 'manifest': digest(frozen),
                       'entries': 0, 'tail': None}, output)
            output.flush()
            os.fsync(output.fileno())
        root.mkdir(mode=0o700)
        durable_json(root / 'frozen.json', frozen)
        (root / 'run.lock').touch(exist_ok=False)
        (root / 'ledger.jsonl').touch(exist_ok=False)
        instance = cls(root)
        instance.append({'kind': 'created', 'experiment': EXPERIMENT, 'cap': CAP,
                         'manifest': digest(frozen)})
        return instance

    @contextmanager
    def locked(self):
        try:
            with self.path.open('r+') as journal:
                fcntl.flock(journal, fcntl.LOCK_EX)
                yield journal
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ResearchStopped('missing or conflicting research history: ' + str(error)) from error

    def read(self, journal):
        anchor = json.loads(self.anchor.read_text())
        frozen = json.loads((self.root / 'frozen.json').read_text())
        if anchor['experiment'] != EXPERIMENT or anchor['manifest'] != digest(frozen):
            raise ResearchStopped('conflicting research identity or frozen manifest')
        journal.seek(0)
        rows, tail = [], None
        for line in journal:
            row = json.loads(line)
            if row['previous'] != tail or row['sequence'] != len(rows) or row['hash'] != digest(
                    {k: v for k, v in row.items() if k != 'hash'}):
                raise ResearchStopped('conflicting research journal')
            rows.append(row)
            tail = row['hash']
        count = anchor['entries']
        if (type(count) is not int or not 0 <= count <= len(rows)
                or anchor['tail'] != (rows[count - 1]['hash'] if count else None)):
            raise ResearchStopped('research history was lost or rolled back')
        if rows and rows[0]['event'] != {'kind': 'created', 'experiment': EXPERIMENT,
                                       'cap': CAP, 'manifest': digest(frozen)}:
            raise ResearchStopped('conflicting research allowance')
        return anchor, rows

    def events(self):
        with self.locked() as journal:
            _, rows = self.read(journal)
            return [r['event'] for r in rows]

    def append(self, event):
        with self.locked() as journal:
            anchor, rows = self.read(journal)
            if callable(event):
                event = event([r['event'] for r in rows])
            row = {'sequence': len(rows), 'previous': rows[-1]['hash'] if rows else None,
                   'event': event}
            row['hash'] = digest(row)
            journal.seek(0, os.SEEK_END)
            journal.write(json.dumps(row, sort_keys=True) + '\n')
            journal.flush()
            os.fsync(journal.fileno())
            # A reservation cannot reach the transport until both durable records exist.
            # A journal ahead of its checkpoint after a crash is retained conservatively.
            durable_json(self.anchor, {**anchor, 'entries': len(rows) + 1, 'tail': row['hash']})
        return event

    @staticmethod
    def calls(events):
        calls = {}
        for event in events:
            if event['kind'] == 'reserve':
                calls[event['id']] = {**event, 'charged': None, 'metrics': None}
            elif event['kind'] == 'settle':
                call = calls[event['id']]
                if call['charged'] is not None:
                    raise ResearchStopped('conflicting research settlement')
                call.update(charged=event['charged'], metrics=event['metrics'])
        return list(calls.values())

    def reserve(self, inputs, outputs, phase, identity, *, model):
        frozen = json.loads((self.root / 'frozen.json').read_text())
        if (model not in frozen['routing']['models'].values()
                or inputs != model['context_tokens'] or outputs not in (256, 1024, 1536, 4096)):
            raise ResearchStopped('reservation differs from frozen routing or token bounds')
        amount = cost(model, inputs, outputs)
        key = uuid.uuid4().hex

        def reservation(events):
            if not events:
                raise ResearchStopped('research allowance unavailable')
            calls = self.calls(events)
            if any(c['charged'] is not None and c['charged'] > c['reserved'] for c in calls):
                raise ResearchStopped('provider usage exceeded a reservation')
            committed = sum(c['reserved'] if c['charged'] is None else c['charged'] for c in calls)
            if committed + amount > CAP:
                raise ResearchStopped('next full-context reservation exceeds research ceiling')
            return {'kind': 'reserve', 'id': key, 'identity': identity, 'phase': phase,
                    'reserved': amount, 'model': model, 'inputs': inputs, 'outputs': outputs,
                    'role': active_role.get(), 'context': dict(self.context), 'time': time.time()}

        self.append(reservation)
        return key

    def reconcile(self, key, envelope, elapsed):
        def settlement(events):
            call = next((c for c in self.calls(events) if c['id'] == key), None)
            if call is None or call['charged'] is not None:
                raise ResearchStopped('invalid research reservation')
            usage = envelope.get('usage', {})
            inputs, outputs = usage.get('inputTokens'), usage.get('outputTokens')
            if any(type(n) is not int or n < 0 for n in (inputs, outputs)):
                raise ResearchStopped('missing usage; full research reservation retained')
            if usage.get('cacheWriteInputTokens', 0) or usage.get('cacheReadInputTokens', 0):
                raise ResearchStopped('unexpected cache usage; full research reservation retained')
            actual = cost(call['model'], inputs, outputs)
            return {'kind': 'settle', 'id': key, 'charged': actual,
                    'metrics': {'usage': usage, 'stop_reason': envelope.get('stopReason'),
                                'latency_seconds': elapsed}}

        event = self.append(settlement)
        call = next(c for c in self.calls(self.events()) if c['id'] == key)
        if event['charged'] > call['reserved']:
            raise ResearchStopped('provider usage exceeded research reservation')

    def summary(self):
        calls = self.calls(self.events())
        charged = sum(c['charged'] or 0 for c in calls)
        outstanding = sum(c['reserved'] for c in calls if c['charged'] is None)
        return {'experiment': EXPERIMENT, 'cap_nanodollars': CAP,
                'charged_nanodollars': charged, 'outstanding_nanodollars': outstanding,
                'remaining_nanodollars': CAP - charged - outstanding, 'calls': calls}
