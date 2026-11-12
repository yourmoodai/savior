"""Repository review limits; neither a revision nor a comment creates dollars."""
from datetime import date, datetime, timezone
import os
import re

PR_LIMIT = 2_000_000_000  # nanodollars: $2 for the lifetime of a PR
DAILY_LIMIT = 5_000_000_000  # nanodollars: $5 per repository per UTC day


def utc_day():
    return datetime.now(timezone.utc).date().isoformat()


def context():
    from budget import BudgetError
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    number = os.environ.get('PR_NUMBER', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or not re.fullmatch(r'[1-9][0-9]*', number):
        raise BudgetError('repository and PR identity required')
    return repository.lower(), int(number)


def validate_attribution(repository, pr, started, settled):
    from budget import BudgetError
    try:
        if (not isinstance(repository, str) or not re.fullmatch(r'[a-z0-9_.-]+/[a-z0-9_.-]+', repository)
                or type(pr) is not int or pr < 0 or date.fromisoformat(started).isoformat() != started
                or started > utc_day() or (settled is not None and (
                    date.fromisoformat(settled).isoformat() != settled or not started <= settled <= utc_day()))):
            raise ValueError()
    except (ValueError, TypeError):
        raise BudgetError('invalid call attribution') from None


def remaining(db, repository, pr):
    from budget import BudgetError
    day = utc_day()
    rows = db.execute('SELECT c.reserved,c.charged,a.repository,a.pr,a.started,a.settled '
                      'FROM calls c LEFT JOIN attribution a ON a.call_id=c.id').fetchall()
    pr_spent, daily_spent = 0, 0
    for reserved, charged, repo, number, started, settled in rows:
        if type(reserved) is not int or reserved < 0 or (charged is not None and (type(charged) is not int or charged < 0)):
            raise BudgetError('invalid historical charge')
        if repo is None:
            raise BudgetError('historical spending lacks PR or UTC attribution; reconciliation required')
        validate_attribution(repo, number, started, settled)
        if (charged is None) != (settled is None) or (charged is not None and charged > reserved):
            raise BudgetError('unresolved or conflicting spending evidence')
        amount = reserved if charged is None else charged
        if repo == repository:
            if number == pr:
                pr_spent += amount
            # An uncertain response can still be billed later. Crossing midnight
            # charges both days; it never releases an in-flight reservation.
            if settled is None or started <= day <= settled:
                daily_spent += amount
    return {'repository': repository, 'pr': pr, 'utc_day': day,
            'pr_limit_nanodollars': PR_LIMIT, 'daily_limit_nanodollars': DAILY_LIMIT,
            'pr_remaining_nanodollars': max(0, PR_LIMIT-pr_spent),
            'daily_remaining_nanodollars': max(0, DAILY_LIMIT-daily_spent)}


def check(amount, allowance):
    from budget import BudgetError, BudgetExhausted
    if type(amount) is not int or amount < 0:
        raise BudgetError('invalid projected cost')
    if amount > min(allowance['pr_remaining_nanodollars'], allowance['daily_remaining_nanodollars']):
        raise BudgetExhausted('projected cost exceeds repository policy')
