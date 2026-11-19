"""Persistent tasks, findings and work orders for exterior inspections."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import os
import sqlite3
import uuid

from fastapi import HTTPException

from deployment_paths import backend_path

ROOT = backend_path('walldata', 'workflow')
DB = ROOT / 'inspection.sqlite3'

KIND_LABELS = {'Crack': '裂缝', 'Seepage': '渗水', 'TileSpalling': '面砖脱落'}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
    building TEXT NOT NULL, facade TEXT NOT NULL, video TEXT NOT NULL,
    subtitle TEXT, offset REAL NOT NULL DEFAULT 0, duration REAL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
    state TEXT NOT NULL, model TEXT NOT NULL, confidence REAL NOT NULL, iou REAL NOT NULL,
    sample_fps REAL NOT NULL, frame_count INTEGER NOT NULL DEFAULT 0,
    position REAL NOT NULL DEFAULT 0, coverage REAL NOT NULL DEFAULT 0,
    error TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
    run_id TEXT REFERENCES runs(id), state TEXT NOT NULL DEFAULT 'pending',
    kind TEXT NOT NULL DEFAULT 'TileSpalling', zone TEXT NOT NULL DEFAULT '',
    floor TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
    first_time REAL NOT NULL, last_time REAL NOT NULL, evidence_time REAL NOT NULL,
    score REAL NOT NULL, bbox TEXT NOT NULL, evidence TEXT, source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS findings_task ON findings(task_id, state, first_time);
CREATE TABLE IF NOT EXISTS work_orders (
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
    state TEXT NOT NULL, assignee TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT 'normal',
    due_on TEXT, instructions TEXT NOT NULL DEFAULT '', completion TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_findings (
    order_id TEXT NOT NULL REFERENCES work_orders(id),
    finding_id TEXT NOT NULL REFERENCES findings(id), PRIMARY KEY(order_id, finding_id)
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
    entity_id TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
    detail TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES work_orders(id),
    kind TEXT NOT NULL, filename TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def new_id():
    return uuid.uuid4().hex


def migrate(conn):
    columns = {row[1] for row in conn.execute('PRAGMA table_info(findings)')}
    if 'kind' not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN kind TEXT NOT NULL DEFAULT 'TileSpalling'")
    if 'zone' not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN zone TEXT NOT NULL DEFAULT ''")


@contextmanager
def database(write=False):
    ROOT.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB, timeout=10)) as conn, conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        conn.executescript(SCHEMA)
        migrate(conn)
        if write:
            conn.execute('BEGIN IMMEDIATE')
        yield conn


def admin(user):
    admins = os.getenv('UAV_WALL_ADMINS', '').split(',')
    return user.casefold() in {name.strip().casefold() for name in admins}


def task_for(conn, task_id, user, write=False):
    task = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    if task is None:
        raise HTTPException(404, '巡检任务不存在')
    if task['owner'] == user or admin(user):
        return task
    assigned = conn.execute(
        'SELECT 1 FROM work_orders WHERE task_id=? AND assignee=? COLLATE NOCASE AND state NOT IN (?, ?)',
        (task_id, user, 'draft', 'cancelled'),
    ).fetchone()
    if write or not assigned:
        raise HTTPException(404, '巡检任务不存在或无权访问')
    return task


def record(conn, task_id, entity_id, user, action, detail=''):
    conn.execute('INSERT INTO activity(task_id,entity_id,actor,action,detail,created_at) VALUES(?,?,?,?,?,?)',
                 (task_id, entity_id, user, action, detail, now()))


def summary(conn, task_id):
    counts = dict(conn.execute('SELECT state, COUNT(*) FROM findings WHERE task_id=? GROUP BY state', (task_id,)))
    work = dict(conn.execute(
        """SELECT o.state, COUNT(DISTINCT f.finding_id) FROM work_orders o
           JOIN order_findings f ON f.order_id=o.id WHERE o.task_id=?
           AND o.state NOT IN ('draft','cancelled') GROUP BY o.state""", (task_id,),
    ))
    return {
        'pending': counts.get('pending', 0),
        'confirmed': counts.get('confirmed', 0),
        'assigned': sum(count for state, count in work.items() if state != 'completed'),
        'completed': work.get('completed', 0),
    }
