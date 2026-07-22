import Database from 'better-sqlite3';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Account } from './types.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const DB_PATH = path.join(ROOT, 'bridge.sqlite3');

export function getAccounts(): Account[] {
  const db = new Database(DB_PATH, { readonly: true });
  try {
    return db
      .prepare('SELECT * FROM accounts WHERE enabled=1 ORDER BY id')
      .all() as Account[];
  } finally {
    db.close();
  }
}

export function getAccountByLabel(label: string): Account | undefined {
  const db = new Database(DB_PATH, { readonly: true });
  try {
    return db
      .prepare('SELECT * FROM accounts WHERE enabled=1 AND label=?')
      .get(label) as Account | undefined;
  } finally {
    db.close();
  }
}