"""关键路径包：标星冻结、工程落点、指纹漂移、续采编排。

两种工程落点互斥（禁止并存）：
- snapshot：不可变快照，标星当下锁死节点序与边指纹（库级触发器禁止改写/删除）。
- shadow：影子草稿表，路径包收在草稿表，供影响页只读引用。

任一时刻只认一个口径（lab_settings.path_landing）；影响分析只跟随该口径下的路径包。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import deque
from contextlib import contextmanager

from app.db import get_db, immediate_db
from app.services.errors import ServiceError

LANDING_SNAPSHOT = "snapshot"
LANDING_SHADOW = "shadow"
LANDINGS = (LANDING_SNAPSHOT, LANDING_SHADOW)
LANDING_LABELS = {
    LANDING_SNAPSHOT: "不可变快照",
    LANDING_SHADOW: "影子草稿",
}
LANDING_TABLES = {
    LANDING_SNAPSHOT: "path_snapshots",
    LANDING_SHADOW: "path_shadow_drafts",
}
SETTING_KEY = "path_landing"


@contextmanager
def write_locked(db_path, action: str):
    """排他写事务；锁争用超时翻译成中文冲突，不落成 500。"""
    try:
        with immediate_db(db_path) as conn:
            yield conn
    except sqlite3.OperationalError as exc:
        text = str(exc).lower()
        if "locked" in text or "busy" in text:
            raise ServiceError(f"{action}碰上排他写锁争用，请稍后重试", 409) from exc
        raise


def landing_label(landing: str) -> str:
    return LANDING_LABELS.get(landing, landing)


def normalize_landing(landing: str) -> str:
    landing = (landing or "").strip().lower()
    if landing not in LANDINGS:
        raise ServiceError(
            f"落点只许取「不可变快照(snapshot)」或「影子草稿(shadow)」，收到「{landing or '空'}」"
        )
    return landing


def active_landing(conn) -> str:
    row = conn.execute(
        "SELECT value FROM lab_settings WHERE key=?", (SETTING_KEY,)
    ).fetchone()
    value = (row["value"] if row else LANDING_SNAPSHOT).strip().lower()
    return value if value in LANDINGS else LANDING_SNAPSHOT


def _decode_ids(raw: str) -> list[int]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return [int(x) for x in data]


def _decode_codes(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return [str(x) for x in data]


def _edge_identity(edge) -> str:
    return (
        f"{edge['edge_code']}|{int(edge['upstream_id'])}>{int(edge['downstream_id'])}"
        f"|{edge['transform_kind']}"
    )


def _fingerprint(identities: list[str]) -> str:
    joined = "\n".join(sorted(identities))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _row_to_package(row) -> dict:
    node_ids = _decode_ids(row["node_ids"])
    node_codes = _decode_codes(row["node_codes"])
    edge_ids = _decode_ids(row["edge_ids"])
    edge_codes = _decode_codes(row["edge_codes"])
    landing = (
        LANDING_SNAPSHOT if row["_table"] == "path_snapshots" else LANDING_SHADOW
    )
    path_rows = []
    for idx, edge_code in enumerate(edge_codes):
        if idx + 1 < len(node_codes):
            path_rows.append(
                {
                    "seq": idx + 1,
                    "edge_code": edge_code,
                    "edge_id": edge_ids[idx] if idx < len(edge_ids) else None,
                    "from": node_codes[idx],
                    "to": node_codes[idx + 1],
                }
            )
    return {
        "path_key": row["path_key"],
        "landing": landing,
        "landing_label": landing_label(landing),
        "root_node_id": int(row["root_node_id"]),
        "node_ids": node_ids,
        "node_codes": node_codes,
        "edge_ids": edge_ids,
        "edge_codes": edge_codes,
        "edge_fingerprint": row["edge_fingerprint"],
        "path_length": int(row["path_length"]),
        "path_rows": path_rows,
        "starred_by": row["starred_by"],
        "starred_at": row["starred_at"],
        "note": row["note"],
        "aligned": int(row["path_length"]) == len(edge_ids) == max(len(node_ids) - 1, 0),
    }


def resolve_path(conn, root_node_id: int, sink_node_id: int) -> tuple[list[int], list[int], list[str]]:
    """确定性最短路（按边 id 稳定排序），只走已发布边。"""
    if int(root_node_id) == int(sink_node_id):
        raise ServiceError("路径起点与终点不能是同一节点")
    for nid in (root_node_id, sink_node_id):
        node = conn.execute(
            "SELECT * FROM lineage_nodes WHERE id=?", (nid,)
        ).fetchone()
        if not node:
            raise ServiceError("路径端点节点不存在", 404)
        if node["status"] == "停用":
            raise ServiceError(f"节点 {node['node_code']} 已停用，不可标为关键路径", 409)
    prev: dict[int, tuple[int, int]] = {}
    seen = {int(root_node_id)}
    queue = deque([int(root_node_id)])
    found = False
    while queue and not found:
        cur = queue.popleft()
        rows = conn.execute(
            "SELECT id, upstream_id, downstream_id FROM lineage_edges "
            "WHERE upstream_id=? AND status='已发布' ORDER BY id",
            (cur,),
        ).fetchall()
        for edge in rows:
            nxt = int(edge["downstream_id"])
            if nxt in seen:
                continue
            seen.add(nxt)
            prev[nxt] = (cur, int(edge["id"]))
            if nxt == int(sink_node_id):
                found = True
                break
            queue.append(nxt)
    if not found:
        raise ServiceError("起点到终点之间没有由已发布边构成的血缘路径，无法标星", 409)

    node_ids = [int(sink_node_id)]
    edge_ids: list[int] = []
    cur = int(sink_node_id)
    while cur != int(root_node_id):
        up, edge_id = prev[cur]
        edge_ids.append(edge_id)
        node_ids.append(up)
        cur = up
    node_ids.reverse()
    edge_ids.reverse()
    codes = []
    for nid in node_ids:
        node = conn.execute(
            "SELECT node_code FROM lineage_nodes WHERE id=?", (nid,)
        ).fetchone()
        codes.append(node["node_code"] if node else f"#{nid}")
    return node_ids, edge_ids, codes


def _frozen_fingerprint(conn, edge_ids: list[int]) -> tuple[str, list[str]]:
    identities = []
    codes = []
    for edge_id in edge_ids:
        row = conn.execute(
            "SELECT * FROM lineage_edges WHERE id=?", (edge_id,)
        ).fetchone()
        if not row:
            identities.append(f"MISSING|{edge_id}")
            continue
        identities.append(_edge_identity(row))
        codes.append(row["edge_code"])
    return _fingerprint(identities), codes


def current_fingerprint(conn, node_ids: list[int]) -> tuple[str, list[str]]:
    """按冻结节点序，用现行已发布边重算指纹；缺边视为漂移。"""
    identities = []
    for up, down in zip(node_ids, node_ids[1:]):
        rows = conn.execute(
            "SELECT * FROM lineage_edges "
            "WHERE upstream_id=? AND downstream_id=? AND status='已发布' ORDER BY id",
            (up, down),
        ).fetchall()
        if not rows:
            identities.append(f"MISSING|{up}>{down}")
            continue
        identities.extend(_edge_identity(r) for r in rows)
    return _fingerprint(identities), []


def drift_of(conn, pkg: dict) -> dict:
    current_fp, _ = current_fingerprint(conn, pkg["node_ids"])
    ok = current_fp == pkg["edge_fingerprint"]
    return {
        "ok": ok,
        "frozen_fingerprint": pkg["edge_fingerprint"],
        "current_fingerprint": current_fp,
        "message": "" if ok else "路径指纹与现行边不一致，已漂移",
    }


def _fetch(conn, table: str, path_key: str):
    row = conn.execute(
        f"SELECT *, ? AS _table FROM {table} WHERE path_key=?", (table, path_key)
    ).fetchone()
    return row


def _fetch_any(conn, path_key: str):
    for landing in LANDINGS:
        row = _fetch(conn, LANDING_TABLES[landing], path_key)
        if row:
            return row
    return None


def star_path(
    root_node_id: int,
    sink_node_id: int,
    landing: str,
    starred_by: str,
    note: str = "",
    db_path: str | None = None,
) -> dict:
    """标星入口：单次落定所选落点；失败抛中文 ServiceError。"""
    landing = normalize_landing(landing)
    actor = (starred_by or "").strip() or "未署名"
    with write_locked(db_path, "关键路径标星") as conn:
        active = active_landing(conn)
        if landing != active:
            raise ServiceError(
                f"当前路径口径为「{landing_label(active)}」，与所选落点"
                f"「{landing_label(landing)}」不一致；请先在台参数切换口径，禁止两种落点并存",
                409,
            )
        node_ids, edge_ids, node_codes = resolve_path(
            conn, int(root_node_id), int(sink_node_id)
        )
        path_key = f"{node_codes[0]}→{node_codes[-1]}"

        existing = _fetch_any(conn, path_key)
        if existing is not None:
            existing_landing = (
                LANDING_SNAPSHOT
                if existing["_table"] == "path_snapshots"
                else LANDING_SHADOW
            )
            if existing_landing == landing:
                raise ServiceError(
                    f"路径 {path_key} 已由 {existing['starred_by']} 于 "
                    f"{existing['starred_at']} 标星冻结（{landing_label(existing_landing)}），"
                    "不可重复标星",
                    409,
                )
            raise ServiceError(
                f"路径 {path_key} 的路径包已冻结在「{landing_label(existing_landing)}」落点，"
                "同一路径只许走一种工程落点，禁止并存",
                409,
            )

        fingerprint, edge_codes = _frozen_fingerprint(conn, edge_ids)
        path_length = len(edge_ids)
        table = LANDING_TABLES[landing]
        if landing == LANDING_SHADOW:
            conn.execute(
                f"INSERT INTO {table}(path_key, root_node_id, node_ids, node_codes, "
                "edge_ids, edge_codes, edge_fingerprint, path_length, status, "
                "starred_by, note) VALUES (?,?,?,?,?,?,?,?,'草稿',?,?)",
                (
                    path_key,
                    int(root_node_id),
                    json.dumps(node_ids),
                    json.dumps(node_codes),
                    json.dumps(edge_ids),
                    json.dumps(edge_codes),
                    fingerprint,
                    path_length,
                    actor,
                    note or "",
                ),
            )
        else:
            conn.execute(
                f"INSERT INTO {table}(path_key, root_node_id, node_ids, node_codes, "
                "edge_ids, edge_codes, edge_fingerprint, path_length, starred_by, note) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    path_key,
                    int(root_node_id),
                    json.dumps(node_ids),
                    json.dumps(node_codes),
                    json.dumps(edge_ids),
                    json.dumps(edge_codes),
                    fingerprint,
                    path_length,
                    actor,
                    note or "",
                ),
            )
        row = _fetch(conn, table, path_key)
        if row is None:
            raise ServiceError("标星写入后未读到路径包，已回滚", 500)
        pkg = _row_to_package(row)
        pkg["drift"] = {"ok": True, "frozen_fingerprint": fingerprint,
                        "current_fingerprint": fingerprint, "message": ""}
        return pkg


def list_packages(landing: str | None = None, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        active = active_landing(conn)
        target = normalize_landing(landing) if landing else active
        counts = {}
        for mode in LANDINGS:
            counts[mode] = int(
                conn.execute(
                    f"SELECT COUNT(*) AS c FROM {LANDING_TABLES[mode]}"
                ).fetchone()["c"]
            )
        items = []
        archived = []
        for mode in LANDINGS:
            rows = conn.execute(
                f"SELECT *, ? AS _table FROM {LANDING_TABLES[mode]} ORDER BY id DESC",
                (LANDING_TABLES[mode],),
            ).fetchall()
            for row in rows:
                pkg = _row_to_package(row)
                pkg["is_active_landing"] = mode == active
                pkg["drift"] = drift_of(conn, pkg)
                (items if mode == active else archived).append(pkg)
        return {
            "landing": active,
            "landing_label": landing_label(active),
            "selected": target,
            "selected_label": landing_label(target),
            "items": items,
            "archived": archived,
            "counts": counts,
        }


def get_package(path_key: str, landing: str | None = None, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        active = active_landing(conn)
        mode = normalize_landing(landing) if landing else active
        row = _fetch(conn, LANDING_TABLES[mode], path_key)
        if row is None:
            raise ServiceError(
                f"当前口径「{landing_label(mode)}」下没有路径包 {path_key}", 404
            )
        pkg = _row_to_package(row)
        pkg["drift"] = drift_of(conn, pkg)
        return pkg


def packages_for_root(conn, root_node_id: int) -> list[dict]:
    mode = active_landing(conn)
    rows = conn.execute(
        f"SELECT *, ? AS _table FROM {LANDING_TABLES[mode]} "
        "WHERE root_node_id=? ORDER BY id DESC",
        (LANDING_TABLES[mode], int(root_node_id)),
    ).fetchall()
    out = []
    for row in rows:
        pkg = _row_to_package(row)
        pkg["drift"] = drift_of(conn, pkg)
        out.append(pkg)
    return out


def edge_chips(db_path: str | None = None) -> dict[int, list[dict]]:
    """边 id -> 路径来源芯片（同一路径包口径，只读派生）。"""
    with get_db(db_path) as conn:
        mode = active_landing(conn)
        rows = conn.execute(
            f"SELECT *, ? AS _table FROM {LANDING_TABLES[mode]}", (LANDING_TABLES[mode],)
        ).fetchall()
        chips: dict[int, list[dict]] = {}
        for row in rows:
            pkg = _row_to_package(row)
            for edge_id in pkg["edge_ids"]:
                chips.setdefault(edge_id, []).append(
                    {
                        "path_key": pkg["path_key"],
                        "landing": pkg["landing"],
                        "landing_label": pkg["landing_label"],
                        "path_length": pkg["path_length"],
                    }
                )
        return chips


def node_marks(db_path: str | None = None) -> dict[int, list[dict]]:
    """节点 id -> 关键路径标记与同源路径长度。"""
    with get_db(db_path) as conn:
        mode = active_landing(conn)
        rows = conn.execute(
            f"SELECT *, ? AS _table FROM {LANDING_TABLES[mode]}", (LANDING_TABLES[mode],)
        ).fetchall()
        marks: dict[int, list[dict]] = {}
        for row in rows:
            pkg = _row_to_package(row)
            for node_id in pkg["node_ids"]:
                marks.setdefault(node_id, []).append(
                    {
                        "path_key": pkg["path_key"],
                        "landing": pkg["landing"],
                        "landing_label": pkg["landing_label"],
                        "path_length": pkg["path_length"],
                    }
                )
        return marks


def orchestrate(path_key: str, actor: str, db_path: str | None = None) -> dict:
    """续采编排：路径指纹与现行边拧了就拦住并留痕。"""
    with write_locked(db_path, "续采编排") as conn:
        mode = active_landing(conn)
        row = _fetch(conn, LANDING_TABLES[mode], path_key)
        if row is None:
            raise ServiceError(
                f"当前口径「{landing_label(mode)}」下没有路径包 {path_key}，无法续采编排",
                404,
            )
        pkg = _row_to_package(row)
        drift = drift_of(conn, pkg)
        if not drift["ok"]:
            message = (
                f"路径 {path_key} 指纹 {pkg['edge_fingerprint']} 与现行边 "
                f"{drift['current_fingerprint']} 不一致，续采编排已拦住；"
                "请以原边号重新发布被作废/缺失的边修复路径，再行编排"
            )
            conn.execute(
                "INSERT INTO impact_orchestrations(root_node_id, job_id, path_key, "
                "path_source, path_length, fingerprint_ok, status, message) "
                "VALUES (?,?,?,?,?,0,'已拦截',?)",
                (
                    pkg["root_node_id"],
                    None,
                    path_key,
                    pkg["landing_label"],
                    pkg["path_length"],
                    message,
                ),
            )
            result = {
                "path_key": path_key,
                "status": "已拦截",
                "message": message,
                "path_source": pkg["landing_label"],
                "path_length": pkg["path_length"],
                "drift": drift,
            }
            return result
        message = (
            f"路径 {path_key} 指纹校验通过（{pkg['edge_fingerprint']}），"
            f"续采编排已登记，同源路径长度 {pkg['path_length']}"
        )
        cur = conn.execute(
            "INSERT INTO impact_orchestrations(root_node_id, job_id, path_key, "
            "path_source, path_length, fingerprint_ok, status, message) "
            "VALUES (?,?,?,?,?,1,'已编排',?)",
            (
                pkg["root_node_id"],
                None,
                path_key,
                pkg["landing_label"],
                pkg["path_length"],
                message,
            ),
        )
        return {
            "id": cur.lastrowid,
            "path_key": path_key,
            "status": "已编排",
            "message": message,
            "path_source": pkg["landing_label"],
            "path_length": pkg["path_length"],
            "drift": drift,
        }


def list_orchestrations(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT o.*, n.node_code FROM impact_orchestrations o "
            "JOIN lineage_nodes n ON n.id=o.root_node_id ORDER BY o.id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
