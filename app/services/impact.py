"""影响分析：有路径包时沿冻结路径开跑，无路径包才走全图。"""
from __future__ import annotations

from app.db import get_db
from app.services import paths as paths_svc
from app.services.errors import ServiceError


def _job_payload(row) -> dict:
    item = dict(row)
    item["path_drift"] = bool(item.get("path_drift"))
    return item


def list_jobs(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT j.*, n.node_code, n.table_name FROM impact_jobs j "
            "JOIN lineage_nodes n ON n.id=j.root_node_id ORDER BY j.id DESC"
        ).fetchall()
        return [_job_payload(r) for r in rows]


def job_detail(job_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        job = conn.execute(
            "SELECT j.*, n.node_code, n.table_name FROM impact_jobs j "
            "JOIN lineage_nodes n ON n.id=j.root_node_id WHERE j.id=?",
            (job_id,),
        ).fetchone()
        if not job:
            raise ServiceError("作业不存在", 404)
        hits = conn.execute(
            "SELECT h.*, n.node_code, n.table_name, n.layer FROM impact_hits h "
            "JOIN lineage_nodes n ON n.id=h.node_id WHERE h.job_id=? ORDER BY h.hop, h.id",
            (job_id,),
        ).fetchall()
        pkg = None
        if job["path_key"]:
            pkg = paths_svc.get_package(
                job["path_key"], job["path_mode"] or None, db_path=db_path
            )
        return {"job": _job_payload(job), "hits": [dict(x) for x in hits], "package": pkg}


def _frozen_hits(conn, pkg: dict) -> dict[int, int]:
    seen: dict[int, int] = {}
    for hop, node_id in enumerate(pkg["node_ids"]):
        seen[int(node_id)] = hop
    return seen


def _full_graph_hits(conn, root_node_id: int, max_hop: int | None) -> dict[int, int]:
    seen = {int(root_node_id): 0}
    queue = [int(root_node_id)]
    while queue:
        cur_id = queue.pop(0)
        hop = seen[cur_id]
        if max_hop is not None and hop >= max_hop:
            continue
        kids = conn.execute(
            "SELECT downstream_id FROM lineage_edges "
            "WHERE upstream_id=? AND status='已发布' ORDER BY id",
            (cur_id,),
        ).fetchall()
        for kid in kids:
            kid_id = int(kid["downstream_id"])
            if kid_id not in seen:
                seen[kid_id] = hop + 1
                queue.append(kid_id)
    return seen


def start_impact(root_node_id: int, db_path: str | None = None) -> dict:
    with paths_svc.write_locked(db_path, "影响开跑") as conn:
        root = conn.execute(
            "SELECT * FROM lineage_nodes WHERE id=?", (root_node_id,)
        ).fetchone()
        if not root:
            raise ServiceError("根节点不存在", 404)
        if root["status"] == "停用":
            raise ServiceError("停用节点不可开影响分析", 409)
        running = conn.execute(
            "SELECT id FROM impact_jobs WHERE root_node_id=? AND status IN ('待执行','执行中')",
            (root_node_id,),
        ).fetchone()
        if running:
            raise ServiceError("该根节点已有在途影响作业", 409)

        landing = paths_svc.active_landing(conn)
        packages = paths_svc.packages_for_root(conn, int(root_node_id))
        pkg = None
        if packages:
            # 同一根节点只许一个口径的路径包；取最近标星的那条。
            pkg = packages[0]
            drift = pkg["drift"]
            if not drift["ok"]:
                raise ServiceError(
                    f"关键路径 {pkg['path_key']} 指纹与现行边不一致"
                    f"（冻结 {drift['frozen_fingerprint']} / 现行 {drift['current_fingerprint']}），"
                    "影响分析已拦住；请先以原边号重新发布被作废/缺失的边修复路径",
                    409,
                )
            hits = _frozen_hits(conn, pkg)
            path_source = pkg["landing_label"]
            path_length = pkg["path_length"]
            path_fingerprint = pkg["edge_fingerprint"]
            path_key = pkg["path_key"]
            message_tail = f"沿关键路径 {path_key}（{path_source}）展开"
        else:
            max_hop_row = conn.execute(
                "SELECT value FROM lab_settings WHERE key='impact_max_hop'"
            ).fetchone()
            try:
                max_hop = int(max_hop_row["value"]) if max_hop_row else 8
            except (TypeError, ValueError):
                max_hop = 8
            hits = _full_graph_hits(conn, int(root_node_id), max_hop)
            path_source = "全图"
            path_length = 0
            path_fingerprint = None
            path_key = None
            message_tail = f"按全图已发布边展开（最大 {max_hop} 跳）"

        seq = conn.execute("SELECT COUNT(*) AS c FROM impact_jobs").fetchone()["c"] + 1
        job_code = f"IMP-{seq:04d}"
        cur = conn.execute(
            "INSERT INTO impact_jobs(job_code, root_node_id, status, message, "
            "path_source, path_mode, path_key, path_length, path_fingerprint, "
            "path_drift, started_at) "
            "VALUES (?,?,'执行中','展开中',?,?,?,?,?,0,datetime('now'))",
            (
                job_code,
                root_node_id,
                path_source,
                landing if pkg else "",
                path_key,
                path_length,
                path_fingerprint,
            ),
        )
        job_id = cur.lastrowid
        for node_id, hop in sorted(hits.items(), key=lambda kv: (kv[1], kv[0])):
            conn.execute(
                "INSERT OR IGNORE INTO impact_hits(job_id, node_id, hop) VALUES (?,?,?)",
                (job_id, node_id, hop),
            )
        affected = len(hits)
        message = f"影响到 {affected} 个节点；{message_tail}"
        conn.execute(
            "UPDATE impact_jobs SET status='成功', affected_count=?, message=?, "
            "finished_at=datetime('now') WHERE id=?",
            (affected, message, job_id),
        )
        return _job_payload(
            conn.execute(
                "SELECT * FROM impact_jobs WHERE id=?", (job_id,)
            ).fetchone()
        )
