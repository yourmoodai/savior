"""Durable reservations prevent concurrent or resumed calls from reusing dollars."""
from contextlib import closing, contextmanager
import json
import os
import sqlite3
import time
import uuid

LIMIT = 10_000_000_000  # nanodollars: $10 pilot, including uncertain calls
QUALIFICATION_LIMIT = 2_000_000_000  # nanodollars: $2
INPUT_RATE = 620  # nanodollars per token; US DeepSeek V3.2 standard on-demand
OUTPUT_RATE = 1850
RESERVED_INPUT_TOKENS = 167936  # 164 Ki tokens; full published context capacity


class BudgetError(Exception):
    pass


class BudgetExhausted(BudgetError):
    pass


def cost(inputs, outputs):
    return inputs * INPUT_RATE + outputs * OUTPUT_RATE


class Ledger:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, phase TEXT, identity TEXT, reserved INTEGER, charged INTEGER, metrics TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS grants (id TEXT PRIMARY KEY, cap INTEGER, context TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS funding (call_id TEXT PRIMARY KEY, grant_id TEXT NOT NULL)')

    def authorize(self, grant):
        """An approval belongs to one logical workflow run, including its retries."""
        identity, cap = grant['id'], grant['cap_nanodollars']
        if not isinstance(identity, str) or not identity or type(cap) is not int or not 0 < cap <= 2**53-1:
            raise BudgetError('invalid review allowance')
        context = json.dumps(grant, sort_keys=True)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT cap,context FROM grants WHERE id=?', (identity,)).fetchone()
            if old and old != (cap, context):
                raise BudgetError('approval already used or changed')
            db.execute('INSERT OR IGNORE INTO grants VALUES (?,?,?)', (identity, cap, context))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def record_recovery(self, identity, amount):
        """Retain conservative recovery charges once, including uncertain usage."""
        if not isinstance(identity, str) or not identity or type(amount) is not int or not 0 < amount <= LIMIT:
            raise BudgetError('invalid recovery charge')
        with self.connect() as db:
            key = 'recovery-' + identity
            row = db.execute('SELECT reserved FROM calls WHERE id=?', (key,)).fetchone()
            if row is not None and row[0] != amount:
                raise BudgetError('conflicting recovery charge')
            db.execute("INSERT OR IGNORE INTO calls VALUES (?, 'qualification', ?, ?, NULL, NULL)",
                       (key, identity, amount))

    def record_preflight(self):
        """Retain the $0.03 reserved for account probes before CI owned the pilot."""
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO calls VALUES (?, 'qualification', ?, ?, NULL, NULL)",
                       ('account-probes-20261015', 'haiku55-and-glm47-access-probes', 30_000_000))

    def merge(self, source):
        """Union settled histories without dropping calls or repricing prior usage."""
        with closing(sqlite3.connect(source)) as prior:
            rows = prior.execute('SELECT id,phase,identity,reserved,charged,metrics FROM calls').fetchall()
            tables = {r[0] for r in prior.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            grants = prior.execute('SELECT id,cap,context FROM grants').fetchall() if 'grants' in tables else []
            funding = dict(prior.execute('SELECT call_id,grant_id FROM funding')) if 'funding' in tables else {}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for identity, cap, context in grants:
                metadata = json.loads(context)
                if (type(cap) is not int or not 0 < cap <= 2**53-1 or metadata.get('id') != identity
                        or metadata.get('cap_nanodollars') != cap):
                    raise BudgetError('invalid historical allowance')
                old = db.execute('SELECT cap,context FROM grants WHERE id=?', (identity,)).fetchone()
                if old and old != (cap, context):
                    raise BudgetError('conflicting historical allowance')
                db.execute('INSERT OR IGNORE INTO grants VALUES (?,?,?)', (identity, cap, context))
            for row in rows:
                key, phase, identity, reserved, charged, metrics = row
                if phase not in ('qualification', 'review') or type(reserved) is not int or reserved < 0 or (charged is not None and (type(charged) is not int or charged < 0)):
                    raise BudgetError('invalid historical ledger row')
                old = db.execute('SELECT id,phase,identity,reserved,charged,metrics FROM calls WHERE id=?', (key,)).fetchone()
                funded = db.execute('SELECT grant_id FROM funding WHERE call_id=?', (key,)).fetchone()
                grant = funding.get(key)
                if old and (funded[0] if funded else None) != grant:
                    raise BudgetError('conflicting historical funding')
                if grant:
                    if not db.execute('SELECT 1 FROM grants WHERE id=?', (grant,)).fetchone():
                        raise BudgetError('missing historical allowance')
                    db.execute('INSERT OR IGNORE INTO funding VALUES (?,?)', (key, grant))
                if old is None:
                    db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?)', row)
                elif key == 'external-probes' and old[1:3] == row[1:3] and old[4] is None and charged is None:
                    db.execute('UPDATE calls SET reserved=MAX(reserved,?) WHERE id=?', (reserved,key))
                elif old[:4] != row[:4] or (old[4] is not None and charged is not None and old[4:] != row[4:]):
                    raise BudgetError('conflicting historical ledger rows')
                elif old[4] is None and charged is not None:
                    db.execute('UPDATE calls SET charged=?,metrics=? WHERE id=?', (charged,metrics,key))

    def record_external_reservation(self, amount):
        """Import cumulative external probe costs without releasing prior reservations."""
        if type(amount) is not int or not 0 <= amount <= LIMIT:
            raise BudgetError('invalid external reservation')
        if amount:
            with self.connect() as db:
                db.execute("INSERT INTO calls VALUES (?, 'qualification', ?, ?, NULL, NULL) "
                           "ON CONFLICT(id) DO UPDATE SET reserved=MAX(reserved,excluded.reserved)",
                           ('external-probes', 'external-qualification-probes', amount))

    def reserve(self, inputs, outputs, phase, identity):
        if phase not in ('qualification', 'review') or any(type(n) is not int or n < 0 for n in (inputs, outputs)):
            raise BudgetError('invalid reservation parameters')
        amount = cost(inputs, outputs)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM calls WHERE charged > reserved').fetchone()[0]:
                raise BudgetError('provider usage exceeded a reservation; pilot halted')
            grant = os.environ.get('REVIEW_BUDGET_GRANT')
            if grant:
                allowance = db.execute('SELECT cap FROM grants WHERE id=?', (grant,)).fetchone()
                if allowance is None:
                    raise BudgetError('review allowance unavailable')
                spent = db.execute('SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) FROM calls JOIN funding ON calls.id=funding.call_id WHERE grant_id=?', (grant,)).fetchone()[0]
                if spent + amount > allowance[0]:
                    raise BudgetExhausted('approved review allowance exhausted')
            else:
                total = db.execute('SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) FROM calls WHERE id NOT IN (SELECT call_id FROM funding)').fetchone()[0]
                qualification = db.execute("SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) FROM calls WHERE phase='qualification' AND id NOT IN (SELECT call_id FROM funding)").fetchone()[0]
                if total + amount > LIMIT or (phase == 'qualification' and qualification + amount > QUALIFICATION_LIMIT):
                    raise BudgetExhausted('pilot budget exhausted; assessment remains incomplete')
            key = uuid.uuid4().hex
            db.execute('INSERT INTO calls VALUES (?,?,?,?,NULL,NULL)', (key, phase, identity, amount))
            if grant:
                db.execute('INSERT INTO funding VALUES (?,?)', (key, grant))
        return key

    def reconcile(self, key, envelope, elapsed):
        usage = envelope.get('usage', {})
        inputs, outputs = usage.get('inputTokens'), usage.get('outputTokens')
        if any(type(n) is not int or n < 0 for n in (inputs, outputs)):
            raise BudgetError('missing usage; full reservation retained')
        if usage.get('cacheReadInputTokens', 0) or usage.get('cacheWriteInputTokens', 0):
            raise BudgetError('unexpected cache usage; full reservation retained')
        metrics = {'usage': usage, 'stop_reason': envelope.get('stopReason'),
                   'latency_seconds': elapsed, 'recorded_at': time.time()}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT reserved,charged FROM calls WHERE id=?', (key,)).fetchone()
            if row is None or row[1] is not None:
                raise BudgetError('invalid reservation')
            actual = cost(inputs, outputs)
            db.execute('UPDATE calls SET charged=?,metrics=? WHERE id=?', (actual, json.dumps(metrics), key))
        if actual > row[0]:
            raise BudgetError('provider usage exceeded reservation; stop review')

    def summary(self):
        with self.connect() as db:
            rows = db.execute('SELECT id,phase,identity,reserved,charged,metrics FROM calls ORDER BY rowid').fetchall()
            funding = dict(db.execute('SELECT call_id,grant_id FROM funding'))
            grants = db.execute('SELECT id,cap,context FROM grants ORDER BY id').fetchall()
        return {'limit_usd': LIMIT / 1e9, 'committed_usd': sum(r[4] if r[4] is not None else r[3] for r in rows) / 1e9,
                'pilot_committed_nanodollars': sum(r[4] if r[4] is not None else r[3] for r in rows if r[0] not in funding),
                'qualification_committed_nanodollars': sum(r[4] if r[4] is not None else r[3] for r in rows if r[0] not in funding and r[1] == 'qualification'),
                'grants': [dict(id=g[0], cap_nanodollars=g[1], context=json.loads(g[2]),
                               committed_nanodollars=sum(r[4] if r[4] is not None else r[3] for r in rows if funding.get(r[0]) == g[0])) for g in grants],
                'calls': [dict(id=r[0], phase=r[1], identity=r[2], reserved_usd=r[3]/1e9,
                               actual_usd=None if r[4] is None else r[4]/1e9,
                               metrics=json.loads(r[5]) if r[5] else None) for r in rows]}
