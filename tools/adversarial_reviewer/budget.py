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
            db.execute('CREATE TABLE IF NOT EXISTS call_pricing (call_id TEXT PRIMARY KEY, model TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS ceiling (id INTEGER PRIMARY KEY CHECK(id=1), amount INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS attribution (call_id TEXT PRIMARY KEY, repository TEXT NOT NULL, pr INTEGER NOT NULL, started TEXT NOT NULL, settled TEXT)')

    def policy_budget(self):
        from cost_policy import context, remaining
        with self.connect() as db:
            return remaining(db, *context())

    def set_ceiling(self, amount):
        """A cumulative investigation cap can tighten, never reset or increase."""
        if type(amount) is not int or amount <= 0:
            raise BudgetError('invalid cumulative ceiling')
        with self.connect() as db:
            db.execute('INSERT INTO ceiling VALUES (1,?) ON CONFLICT(id) DO UPDATE SET amount=MIN(amount,excluded.amount)', (amount,))

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
            pricing = dict(prior.execute('SELECT call_id,model FROM call_pricing')) if 'call_pricing' in tables else {}
            ceiling = prior.execute('SELECT amount FROM ceiling WHERE id=1').fetchone() if 'ceiling' in tables else None
            attribution = {r[0]: r[1:] for r in prior.execute('SELECT call_id,repository,pr,started,settled FROM attribution')} if 'attribution' in tables else {}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if ceiling:
                db.execute('INSERT INTO ceiling VALUES (1,?) ON CONFLICT(id) DO UPDATE SET amount=MIN(amount,excluded.amount)', ceiling)
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
                old_attribution = db.execute('SELECT repository,pr,started,settled FROM attribution WHERE call_id=?', (key,)).fetchone()
                attributed = attribution.get(key)
                if attributed:
                    from cost_policy import validate_attribution
                    validate_attribution(*attributed)
                    if (charged is None) != (attributed[3] is None):
                        raise BudgetError('conflicting historical settlement')
                    if old_attribution and (old_attribution[:3] != attributed[:3] or (
                            old_attribution[3] is not None and attributed[3] is not None and old_attribution[3] != attributed[3])):
                        raise BudgetError('conflicting historical attribution')
                    db.execute('INSERT INTO attribution VALUES (?,?,?,?,?) ON CONFLICT(call_id) DO UPDATE SET settled=COALESCE(settled,excluded.settled)', (key, *attributed))
                if phase not in ('qualification', 'review') or type(reserved) is not int or reserved < 0 or (charged is not None and (type(charged) is not int or charged < 0)):
                    raise BudgetError('invalid historical ledger row')
                old = db.execute('SELECT id,phase,identity,reserved,charged,metrics FROM calls WHERE id=?', (key,)).fetchone()
                funded = db.execute('SELECT grant_id FROM funding WHERE call_id=?', (key,)).fetchone()
                grant = funding.get(key)
                model = pricing.get(key)
                old_model = db.execute('SELECT model FROM call_pricing WHERE call_id=?', (key,)).fetchone()
                if old and (old_model[0] if old_model else None) != model:
                    raise BudgetError('conflicting historical model pricing')
                if model:
                    db.execute('INSERT OR IGNORE INTO call_pricing VALUES (?,?)', (key, model))
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

    def reserve(self, inputs, outputs, phase, identity, *, model=None):
        if phase not in ('qualification', 'review') or any(type(n) is not int or n < 0 for n in (inputs, outputs)):
            raise BudgetError('invalid reservation parameters')
        from model_catalog import cost as model_cost
        amount = model_cost(model, inputs, outputs) if model else cost(inputs, outputs)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            ceiling = db.execute('SELECT amount FROM ceiling WHERE id=1').fetchone()
            if ceiling:
                committed = db.execute('SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) FROM calls').fetchone()[0]
                if committed + amount > ceiling[0]:
                    raise BudgetExhausted('cumulative investigation allowance exhausted')
            if db.execute('SELECT COUNT(*) FROM calls WHERE charged > reserved').fetchone()[0]:
                raise BudgetError('provider usage exceeded a reservation; pilot halted')
            policy = os.environ.get('REVIEW_COST_POLICY') == 'true'
            grant = None if policy else os.environ.get('REVIEW_BUDGET_GRANT')
            if policy:
                from cost_policy import context, remaining, check, utc_day
                repository, pr = context()
                check(amount, remaining(db, repository, pr))
            elif grant:
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
            if policy:
                db.execute('INSERT INTO attribution VALUES (?,?,?,?,NULL)', (key, repository, pr, utc_day()))
            if grant:
                db.execute('INSERT INTO funding VALUES (?,?)', (key, grant))
            if model:
                db.execute('INSERT INTO call_pricing VALUES (?,?)', (key, json.dumps(model, sort_keys=True)))
        return key

    def reconcile(self, key, envelope, elapsed):
        usage = envelope.get('usage', {})
        inputs, outputs = usage.get('inputTokens'), usage.get('outputTokens')
        if any(type(n) is not int or n < 0 for n in (inputs, outputs)):
            raise BudgetError('missing usage; full reservation retained')
        if usage.get('cacheWriteInputTokens', 0):
            raise BudgetError('unexpected cache usage; full reservation retained')
        metrics = {'usage': usage, 'stop_reason': envelope.get('stopReason'),
                   'latency_seconds': elapsed, 'recorded_at': time.time()}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT reserved,charged FROM calls WHERE id=?', (key,)).fetchone()
            if row is None or row[1] is not None:
                raise BudgetError('invalid reservation')
            from model_catalog import cost as model_cost
            model = db.execute('SELECT model FROM call_pricing WHERE call_id=?', (key,)).fetchone()
            cached = usage.get('cacheReadInputTokens', 0)
            if type(cached) is not int or not 0 <= cached <= inputs or (cached and (
                    not model or json.loads(model[0]).get('cache_read_pricing') != 'standard_input_upper_bound')):
                raise BudgetError('unexpected cache usage; full reservation retained')
            actual = model_cost(json.loads(model[0]), inputs, outputs) if model else cost(inputs, outputs)
            if cached:
                metrics['cost_basis'] = 'standard_input_upper_bound'
            if model:
                metrics['model'] = json.loads(model[0])
            db.execute('UPDATE calls SET charged=?,metrics=? WHERE id=?', (actual, json.dumps(metrics), key))
            from cost_policy import utc_day
            db.execute('UPDATE attribution SET settled=? WHERE call_id=?', (utc_day(), key))
        if actual > row[0]:
            raise BudgetError('provider usage exceeded reservation; stop review')

    def summary(self):
        with self.connect() as db:
            rows = db.execute('SELECT id,phase,identity,reserved,charged,metrics FROM calls ORDER BY rowid').fetchall()
            funding = dict(db.execute('SELECT call_id,grant_id FROM funding'))
            grants = db.execute('SELECT id,cap,context FROM grants ORDER BY id').fetchall()
            ceiling = db.execute('SELECT amount FROM ceiling WHERE id=1').fetchone()
        return {'ceiling_nanodollars': ceiling[0] if ceiling else None,
                'settled_usage_nanodollars': sum(r[4] for r in rows if r[4] is not None),
                'outstanding_reserved_nanodollars': sum(r[3] for r in rows if r[4] is None),
                'limit_usd': LIMIT / 1e9, 'committed_usd': sum(r[4] if r[4] is not None else r[3] for r in rows) / 1e9,
                'pilot_committed_nanodollars': sum(r[4] if r[4] is not None else r[3] for r in rows if r[0] not in funding),
                'qualification_committed_nanodollars': sum(r[4] if r[4] is not None else r[3] for r in rows if r[0] not in funding and r[1] == 'qualification'),
                'grants': [dict(id=g[0], cap_nanodollars=g[1], context=json.loads(g[2]),
                               committed_nanodollars=sum(r[4] if r[4] is not None else r[3] for r in rows if funding.get(r[0]) == g[0])) for g in grants],
                'calls': [dict(id=r[0], phase=r[1], identity=r[2], reserved_usd=r[3]/1e9,
                               actual_usd=None if r[4] is None else r[4]/1e9,
                               metrics=json.loads(r[5]) if r[5] else None) for r in rows]}
