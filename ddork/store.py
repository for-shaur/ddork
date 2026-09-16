"""SQLite seen/new tracking. ~70 lines, one file, no ORM.

Key: findings are keyed on (domain, url). first_seen is set on insert; last_seen on every
run. `--new-only` selects rows whose first_seen run_id equals the current run.
"""
import sqlite3
import time


class Store:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self._init()

    def _init(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS findings (
                domain TEXT NOT NULL,
                url TEXT NOT NULL,
                label TEXT NOT NULL,
                confidence REAL NOT NULL,
                decision_path TEXT,
                source TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                run_id TEXT NOT NULL,
                PRIMARY KEY (domain, url)
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                seed TEXT,
                started TEXT NOT NULL,
                targets INTEGER NOT NULL
            );
        """)
        self.conn.commit()

    def record(self, results, seed=None):
        run_id = str(int(time.time() * 1000))
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.conn.execute("INSERT INTO runs VALUES (?,?,?,?)",
                          (run_id, seed or "", now, len(results)))
        for r in results:
            for f in r.get("findings", []):
                self.conn.execute("""
                    INSERT INTO findings
                        (domain, url, label, confidence, decision_path, source,
                         first_seen, last_seen, run_id)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(domain, url) DO UPDATE SET
                        last_seen=excluded.last_seen,
                        label=excluded.label,
                        confidence=excluded.confidence,
                        decision_path=excluded.decision_path
                """, (f["domain"], f["url"], f["label"], f["confidence"],
                      f.get("decision_path") or "", f.get("source") or "",
                      now, now, run_id))
        self.conn.commit()
        return run_id

    def filter_new(self, results, run_id):
        out = []
        for r in results:
            fresh = []
            for f in r.get("findings", []):
                row = self.conn.execute(
                    "SELECT run_id FROM findings WHERE domain=? AND url=?",
                    (f["domain"], f["url"])
                ).fetchone()
                if row and row[0] == run_id:
                    fresh.append(f)
            if fresh:
                out.append({"domain": r["domain"], "findings": fresh})
        return out

    def close(self):
        self.conn.close()