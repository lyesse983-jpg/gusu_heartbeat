"""SQLite is the source of truth for messages, wake appointments and work."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import uuid


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value=None):
    return (value or utcnow()).astimezone(timezone.utc).isoformat()


def ident():
    return uuid.uuid4().hex


class StaleRun(Exception):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'photos').mkdir(exist_ok=True)
        self.path = self.root / 'companion.sqlite3'
        with self.db() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,role TEXT,content TEXT,created_at TEXT,kind TEXT,run_id TEXT);
            CREATE TABLE IF NOT EXISTS activities(id TEXT PRIMARY KEY,kind TEXT,detail TEXT,created_at TEXT,run_id TEXT);
            CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,key TEXT UNIQUE,content TEXT,source TEXT,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,title TEXT,detail TEXT,status TEXT,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS photos(id TEXT PRIMARY KEY,title TEXT,caption TEXT,filename TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,title TEXT,content TEXT,created_at TEXT,run_id TEXT);
            CREATE TABLE IF NOT EXISTS wakes(id TEXT PRIMARY KEY,due_at TEXT,purpose TEXT,status TEXT,revision INTEGER,created_at TEXT);
            CREATE INDEX IF NOT EXISTS wakes_due ON wakes(status,due_at);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,kind TEXT,status TEXT,created_at TEXT,finished_at TEXT);
            CREATE TABLE IF NOT EXISTS usage(day TEXT PRIMARY KEY,calls INTEGER NOT NULL);
            ''')
            c.execute("INSERT OR IGNORE INTO meta VALUES ('revision','0')")
            c.execute("INSERT OR IGNORE INTO meta VALUES ('settings',?)", (json.dumps({'enabled': True, 'quiet_start': '23:00', 'quiet_end': '08:00', 'daily_budget': 24}),))

    def recover_interrupted(self):
        # Call only after acquiring the runtime lock. Never replay partly emitted actions.
        with self.db() as c:
            runs = c.execute("UPDATE runs SET status='interrupted',finished_at=? WHERE status='running'", (stamp(),)).rowcount
            c.execute("UPDATE wakes SET status='interrupted' WHERE status='running'")
        if runs:
            self.activity('recovery', f'发现 {runs} 次未完成运行，标记为中断；保留已完成操作，未自动重放。')

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def rows(self, sql, args=()):
        with self.db() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def revision(self):
        return int(self.rows("SELECT value FROM meta WHERE key='revision'")[0]['value'])

    @staticmethod
    def _guard(c, revision):
        if revision is not None:
            actual = int(c.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            if actual != revision:
                raise StaleRun('New user input invalidated this background run')

    def settings(self):
        return json.loads(self.rows("SELECT value FROM meta WHERE key='settings'")[0]['value'])

    def update_settings(self, patch):
        settings = self.settings()
        settings.update(patch)
        with self.db() as c:
            c.execute("UPDATE meta SET value=? WHERE key='settings'", (json.dumps(settings),))
        return settings

    def add_user(self, content):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            revision = int(c.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]) + 1
            c.execute("UPDATE meta SET value=? WHERE key='revision'", (str(revision),))
            c.execute("UPDATE wakes SET status='cancelled' WHERE status='pending'")
            mid = ident()
            c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)', (mid, 'user', content, stamp(), 'chat', None))
        return revision

    def add_assistant(self, content, kind, run_id, revision):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            self._guard(c, revision)
            if c.execute('SELECT 1 FROM messages WHERE run_id=? AND kind=?', (run_id, kind)).fetchone():
                return False
            c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)', (ident(), 'assistant', content, stamp(), kind, run_id))
        return True

    def activity(self, kind, detail, run_id=None):
        with self.db() as c:
            c.execute('INSERT INTO activities VALUES (?,?,?,?,?)', (ident(), kind, detail, stamp(), run_id))

    def start_run(self, kind):
        run_id = ident()
        with self.db() as c:
            c.execute('INSERT INTO runs VALUES (?,?,?,?,?)', (run_id, kind, 'running', stamp(), None))
        return run_id

    def finish_run(self, run_id, status):
        with self.db() as c:
            c.execute('UPDATE runs SET status=?,finished_at=? WHERE id=?', (status, stamp(), run_id))

    def schedule(self, due_at, purpose, revision):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            self._guard(c, revision)
            c.execute("UPDATE wakes SET status='replaced' WHERE status='pending'")
            wid = ident()
            c.execute('INSERT INTO wakes VALUES (?,?,?,?,?,?)', (wid, stamp(due_at), purpose, 'pending', revision, stamp()))
        return {'id': wid, 'due_at': stamp(due_at), 'purpose': purpose}

    def next_wake(self):
        rows = self.rows("SELECT id,due_at,purpose FROM wakes WHERE status='pending' ORDER BY due_at LIMIT 1")
        return rows[0] if rows else None

    def cancel_wakes(self, revision=None):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            self._guard(c, revision)
            c.execute("UPDATE wakes SET status='cancelled' WHERE status='pending'")

    def claim_due(self, now=None):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute("SELECT * FROM wakes WHERE status='pending' AND due_at<=? ORDER BY due_at LIMIT 1", (stamp(now),)).fetchone()
            if not row:
                return None
            c.execute("UPDATE wakes SET status='running' WHERE id=?", (row['id'],))
            return dict(row)

    def finish_wake(self, wid, status):
        with self.db() as c:
            c.execute('UPDATE wakes SET status=? WHERE id=?', (status, wid))

    def consume_call(self, day, budget):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            used = c.execute('SELECT calls FROM usage WHERE day=?', (day,)).fetchone()
            if used and used[0] >= budget:
                return False
            c.execute('INSERT INTO usage VALUES (?,1) ON CONFLICT(day) DO UPDATE SET calls=calls+1', (day,))
        return True

    def usage(self, day):
        result = self.rows('SELECT calls FROM usage WHERE day=?', (day,))
        return result[0]['calls'] if result else 0

    def memory(self, key, content, source='user', revision=None):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            self._guard(c, revision)
            c.execute('INSERT INTO memories VALUES (?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET content=excluded.content,source=excluded.source,updated_at=excluded.updated_at', (ident(), key, content, source, stamp()))
        return {'key': key, 'content': content, 'source': source}

    def plan(self, title, detail, status='open', plan_id=None, revision=None):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            self._guard(c, revision)
            pid = plan_id or ident()
            c.execute('INSERT INTO plans VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,detail=excluded.detail,status=excluded.status,updated_at=excluded.updated_at', (pid, title, detail, status, stamp()))
        return {'id': pid, 'title': title, 'detail': detail, 'status': status}

    def artifact(self, title, content, run_id, revision):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            self._guard(c, revision)
            aid = ident()
            c.execute('INSERT INTO artifacts VALUES (?,?,?,?,?)', (aid, title, content, stamp(), run_id))
        return {'id': aid, 'title': title, 'content': content}

    def photo(self, photo_id):
        result = self.rows('SELECT * FROM photos WHERE id=?', (photo_id,))
        return result[0] if result else None

    def add_photo(self, photo_id, title, caption, filename):
        with self.db() as c:
            c.execute('INSERT INTO photos VALUES (?,?,?,?,?)', (photo_id, title, caption, filename, stamp()))

    def delete(self, table, item_id):
        if table not in ('memories', 'plans', 'photos'):
            raise ValueError('Unsupported collection')
        with self.db() as c:
            return c.execute(f'DELETE FROM {table} WHERE id=?', (item_id,)).rowcount > 0

    def recall(self, query, limit=8):
        # Literal substring search supports Chinese with no segmentation dependency.
        term = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        return {
            'messages': self.rows("SELECT id,role,content,created_at FROM messages WHERE content LIKE ? ESCAPE '\\' ORDER BY created_at DESC LIMIT ?", (term, limit)),
            'memories': self.rows("SELECT * FROM memories WHERE key LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\' ORDER BY updated_at DESC LIMIT ?", (term, term, limit)),
        }

    def snapshot(self):
        return {
            'messages': list(reversed(self.rows('SELECT id,role,content,created_at,kind FROM messages ORDER BY created_at DESC LIMIT 100'))),
            'activities': self.rows('SELECT * FROM activities ORDER BY created_at DESC LIMIT 150'),
            'photos': [{k: v for k, v in p.items() if k != 'filename'} | {'url': f"/api/photos/{p['id']}/image"} for p in self.rows('SELECT * FROM photos ORDER BY created_at DESC LIMIT 100')],
            'memories': self.rows('SELECT * FROM memories ORDER BY updated_at DESC LIMIT 100'),
            'plans': self.rows('SELECT * FROM plans ORDER BY updated_at DESC LIMIT 100'),
            'artifacts': self.rows('SELECT * FROM artifacts ORDER BY created_at DESC LIMIT 30'),
            'next_wake': self.next_wake(), 'settings': self.settings(),
        }

    def usage_today(self, day):
        rows = self.rows('SELECT calls FROM usage WHERE day=?', (day,))
        return rows[0]['calls'] if rows else 0
