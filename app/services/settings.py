"""台参数：影响深度、通知渠道、关键路径落点口径。"""
from __future__ import annotations

from app.db import get_db
from app.services import paths as paths_svc
from app.services.errors import ServiceError


def list_settings(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value, label FROM lab_settings ORDER BY key"
        ).fetchall()
        items = [dict(r) for r in rows]
        for item in items:
            if item["key"] == paths_svc.SETTING_KEY:
                item["options"] = [
                    {"value": mode, "label": paths_svc.landing_label(mode)}
                    for mode in paths_svc.LANDINGS
                ]
                item["value_label"] = paths_svc.landing_label(item["value"])
        return items


def _guard_landing_switch(conn, value: str) -> None:
    value = paths_svc.normalize_landing(value)
    other = [m for m in paths_svc.LANDINGS if m != value]
    for mode in other:
        table = paths_svc.LANDING_TABLES[mode]
        count = int(
            conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        )
        if count:
            raise ServiceError(
                f"「{paths_svc.landing_label(mode)}」落点下仍有 {count} 条路径包，"
                f"切换到「{paths_svc.landing_label(value)}」会造成两种落点并存，已拦住；"
                "关键路径标星单次落定，路径包不可迁移",
                409,
            )


def update_setting(key: str, value: str, db_path: str | None = None) -> dict:
    key = (key or "").strip()
    value = (value or "").strip()
    if not key or not value:
        raise ServiceError("参数键与值不能为空")
    with paths_svc.write_locked(db_path, "台参数保存") as conn:
        row = conn.execute(
            "SELECT * FROM lab_settings WHERE key=?", (key,)
        ).fetchone()
        if not row:
            raise ServiceError("参数不存在", 404)
        if key == paths_svc.SETTING_KEY:
            _guard_landing_switch(conn, value)
            value = value.strip().lower()
        conn.execute(
            "UPDATE lab_settings SET value=? WHERE key=?", (value, key)
        )
        item = dict(
            conn.execute(
                "SELECT key, value, label FROM lab_settings WHERE key=?", (key,)
            ).fetchone()
        )
        if key == paths_svc.SETTING_KEY:
            item["value_label"] = paths_svc.landing_label(item["value"])
        return item
