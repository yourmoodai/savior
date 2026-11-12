"""Apply reviewed attribution evidence without changing any historical charge."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from budget import BudgetError
from cost_policy import validate_attribution


def fingerprint(rows):
    return hashlib.sha256(json.dumps(sorted(rows), separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def attribute(ledger, evidence=None):
    if evidence is None:
        evidence = json.loads(Path(__file__).with_name('history.json').read_text())
    if evidence.get('schema') != 1:
        raise BudgetError('invalid attribution evidence')
    with ledger.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute('SELECT id,phase,identity,reserved,charged,metrics FROM calls').fetchall()
        for group in evidence['groups']:
            if 'ids' in group:
                selected = [r for r in rows if r[0] in group['ids']]
            else:
                start = datetime.fromisoformat(group['start']).timestamp()
                end = datetime.fromisoformat(group['end']).timestamp()
                selected = [r for r in rows if r[5] and start <= json.loads(r[5])['recorded_at'] <= end]
            if not selected:
                continue
            if len(selected) != group['count'] or fingerprint(selected) != group['rows_sha256']:
                raise BudgetError('historical attribution evidence does not match ledger')
            for row in selected:
                settled = datetime.fromtimestamp(json.loads(row[5])['recorded_at'],timezone.utc).date().isoformat() if row[4] is not None else None
                values = (evidence['repository'],group['pr'],group['start'][:10],settled)
                validate_attribution(*values)
                old = db.execute('SELECT repository,pr,started,settled FROM attribution WHERE call_id=?',(row[0],)).fetchone()
                if old and old != values:
                    raise BudgetError('conflicting historical attribution')
                db.execute('INSERT OR IGNORE INTO attribution VALUES (?,?,?,?,?)',(row[0],*values))
