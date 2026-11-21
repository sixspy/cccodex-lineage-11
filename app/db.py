import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS lineage_nodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  node_code TEXT NOT NULL UNIQUE,
  table_name TEXT NOT NULL,
  layer TEXT NOT NULL CHECK (layer IN ('ODS','DWD','DWS','ADS')),
  owner_team TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK (status IN ('登记','在用','停用')),
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lineage_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  edge_code TEXT NOT NULL UNIQUE,
  upstream_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  downstream_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  transform_kind TEXT NOT NULL DEFAULT '直抽',
  status TEXT NOT NULL CHECK (status IN ('草稿','已发布','已作废')),
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(upstream_id, downstream_id)
);

CREATE TABLE IF NOT EXISTS impact_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_code TEXT NOT NULL UNIQUE,
  root_node_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  status TEXT NOT NULL CHECK (status IN ('待执行','执行中','成功','失败')),
  affected_count INTEGER NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '',
  path_source TEXT NOT NULL DEFAULT '全图',
  path_mode TEXT NOT NULL DEFAULT '',
  path_key TEXT,
  path_length INTEGER NOT NULL DEFAULT 0,
  path_fingerprint TEXT,
  path_drift INTEGER NOT NULL DEFAULT 0,
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS impact_hits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES impact_jobs(id),
  node_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  hop INTEGER NOT NULL DEFAULT 0,
  UNIQUE(job_id, node_id)
);

CREATE TABLE IF NOT EXISTS impact_orchestrations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  root_node_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  job_id INTEGER REFERENCES impact_jobs(id),
  path_key TEXT,
  path_source TEXT NOT NULL DEFAULT '全图',
  path_length INTEGER NOT NULL DEFAULT 0,
  fingerprint_ok INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL CHECK (status IN ('已编排','已拦截')),
  message TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notify_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES impact_jobs(id),
  node_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  channel TEXT NOT NULL DEFAULT '站内',
  status TEXT NOT NULL CHECK (status IN ('待发','已发','失败')),
  body TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS coverage_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  summary TEXT NOT NULL,
  node_count INTEGER NOT NULL,
  edge_count INTEGER NOT NULL,
  orphan_count INTEGER NOT NULL,
  uncovered_edges INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lab_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  label TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- 工程落点一：不可变关键路径快照。标星当下锁死节点序与边指纹。
CREATE TABLE IF NOT EXISTS path_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path_key TEXT NOT NULL UNIQUE,
  root_node_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  node_ids TEXT NOT NULL,
  node_codes TEXT NOT NULL,
  edge_ids TEXT NOT NULL,
  edge_codes TEXT NOT NULL,
  edge_fingerprint TEXT NOT NULL,
  path_length INTEGER NOT NULL,
  starred_by TEXT NOT NULL,
  starred_at TEXT NOT NULL DEFAULT (datetime('now')),
  note TEXT NOT NULL DEFAULT ''
);

-- 工程落点二：影子草稿表。路径包收在草稿表，供影响页只读引用。
CREATE TABLE IF NOT EXISTS path_shadow_drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path_key TEXT NOT NULL UNIQUE,
  root_node_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  node_ids TEXT NOT NULL,
  node_codes TEXT NOT NULL,
  edge_ids TEXT NOT NULL,
  edge_codes TEXT NOT NULL,
  edge_fingerprint TEXT NOT NULL,
  path_length INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('草稿','生效','失效')) DEFAULT '草稿',
  starred_by TEXT NOT NULL,
  starred_at TEXT NOT NULL DEFAULT (datetime('now')),
  note TEXT NOT NULL DEFAULT ''
);

CREATE TRIGGER IF NOT EXISTS trg_path_snapshots_no_update
BEFORE UPDATE ON path_snapshots
BEGIN
  SELECT RAISE(ABORT, '关键路径快照不可变，已冻结的节点序与边指纹禁止改写');
END;

CREATE TRIGGER IF NOT EXISTS trg_path_snapshots_no_delete
BEFORE DELETE ON path_snapshots
BEGIN
  SELECT RAISE(ABORT, '关键路径快照不可变，禁止删除已冻结的路径包');
END;
"""

DROPPABLE_TABLES = (
    "impact_orchestrations",
    "notify_logs",
    "impact_hits",
    "impact_jobs",
    "coverage_reports",
    "path_snapshots",
    "path_shadow_drafts",
    "lineage_edges",
    "lineage_nodes",
    "lab_settings",
    "meta",
)


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or Config.database()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.Error:
        pass
    return conn


@contextmanager
def get_db(db_path: str | None = None):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def immediate_db(db_path: str | None = None):
    """写事务：BEGIN IMMEDIATE 先拿排他写锁，防止两名仓管双成功。"""
    conn = connect(db_path)
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def init_schema(db_path: str | None = None) -> None:
    with get_db(db_path) as conn:
        conn.executescript(SCHEMA)
