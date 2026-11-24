import {DatabaseSync} from 'node:sqlite';

const pr_limit = 2_000_000; // $2 in millionths of a US dollar, across all revisions

export class Reservations {
  constructor(path) {
    this.db = new DatabaseSync(path);
    this.db.exec('PRAGMA busy_timeout=5000');
    this.db.exec('CREATE TABLE IF NOT EXISTS reservations ('+
      'repository INTEGER NOT NULL, pr INTEGER NOT NULL, call TEXT NOT NULL, '+
      'reserved INTEGER NOT NULL, charged INTEGER, receipt TEXT UNIQUE, PRIMARY KEY(repository, pr, call))');
  }

  reserve(repo, pr, call, upper_bound) {
    if (![repo,pr,upper_bound].every(n => Number.isSafeInteger(n) && n > 0) ||
        typeof call !== 'string' || !call.length) throw Error('invalid reservation');
    this.db.exec('BEGIN IMMEDIATE');
    try {
      const {used} = this.db.prepare('SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) AS used '+
        'FROM reservations WHERE repository=? AND pr=?').get(repo,pr);
      if (upper_bound > pr_limit-used) throw Error('per-pr limit reached');
      this.db.prepare('INSERT INTO reservations(repository,pr,call,reserved) VALUES(?,?,?,?)').run(repo,pr,call,upper_bound);
      this.db.exec('COMMIT');
    } catch (error) {
      this.db.exec('ROLLBACK');
      throw error;
    }
  }

  settle(repo, pr, call, charge, receipt) {
    if (!Number.isSafeInteger(charge) || charge < 0 || typeof receipt !== 'string' || !receipt) throw Error('invalid receipt');
    const row = this.db.prepare('UPDATE reservations SET charged=?,receipt=? WHERE repository=? AND pr=? AND call=? '+
      'AND reserved>=? AND (charged IS NULL OR (charged=? AND receipt=?))').run(charge,receipt,repo,pr,call,charge,charge,receipt);
    if (row.changes !== 1) throw Error('unattributable usage');
  }
}
