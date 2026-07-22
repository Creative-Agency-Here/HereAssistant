import Database from 'better-sqlite3';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const DB_PATH = path.join(ROOT, 'bridge.sqlite3');
export function getAccounts() {
    const db = new Database(DB_PATH, { readonly: true });
    try {
        return db
            .prepare('SELECT * FROM accounts WHERE enabled=1 ORDER BY id')
            .all();
    }
    finally {
        db.close();
    }
}
export function getAccountByLabel(label) {
    const db = new Database(DB_PATH, { readonly: true });
    try {
        return db
            .prepare('SELECT * FROM accounts WHERE enabled=1 AND label=?')
            .get(label);
    }
    finally {
        db.close();
    }
}
//# sourceMappingURL=db.js.map