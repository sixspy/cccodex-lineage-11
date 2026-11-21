import threading

import pytest

from app.services import edges, impact, nodes, paths, settings
from app.services.errors import ServiceError


def _id(db_path, code):
    return int(next(n for n in nodes.list_nodes(db_path=db_path) if n["node_code"] == code)["id"])


def _star(db_path, root="N-ODS-ORD", sink="N-ADS-DASH", landing="snapshot", actor="仓管甲"):
    return paths.star_path(
        _id(db_path, root),
        _id(db_path, sink),
        landing,
        actor,
        db_path=db_path,
    )


def test_star_freezes_node_order_and_fingerprint(db_path):
    pkg = _star(db_path)
    assert pkg["node_codes"] == ["N-ODS-ORD", "N-DWD-ORD", "N-DWS-REV", "N-ADS-DASH"]
    assert pkg["path_length"] == len(pkg["edge_ids"]) == len(pkg["node_codes"]) - 1
    assert pkg["aligned"] is True
    assert pkg["landing"] == "snapshot"
    assert pkg["landing_label"] == "不可变快照"
    assert pkg["edge_fingerprint"]
    assert [r["seq"] for r in pkg["path_rows"]] == [1, 2, 3]
    assert pkg["path_rows"][0]["from"] == "N-ODS-ORD"
    assert pkg["path_rows"][-1]["to"] == "N-ADS-DASH"


def test_star_is_single_shot(db_path):
    _star(db_path)
    with pytest.raises(ServiceError) as exc:
        _star(db_path, actor="仓管乙")
    assert exc.value.status_code == 409
    assert "不可重复标星" in exc.value.message


def test_landing_must_match_active_and_no_coexistence(db_path):
    with pytest.raises(ServiceError) as exc:
        _star(db_path, landing="shadow")
    assert "不一致" in exc.value.message
    _star(db_path)
    # 另一落点即便切换口径也被已有路径包拦住
    with pytest.raises(ServiceError) as exc:
        settings.update_setting("path_landing", "shadow", db_path=db_path)
    assert exc.value.status_code == 409
    assert "并存" in exc.value.message
    listing = paths.list_packages(db_path=db_path)
    assert listing["counts"]["snapshot"] == 1
    assert listing["counts"]["shadow"] == 0


def test_shadow_landing_holds_package_for_impact_read(db_path):
    settings.update_setting("path_landing", "shadow", db_path=db_path)
    pkg = _star(db_path, landing="shadow")
    assert pkg["landing"] == "shadow"
    assert pkg["landing_label"] == "影子草稿"
    detail = paths.get_package(pkg["path_key"], db_path=db_path)
    assert detail["path_length"] == 3
    # 影响页只读引用同一口径的路径包
    job = impact.start_impact(pkg["root_node_id"], db_path=db_path)
    assert job["path_source"] == "影子草稿"
    assert job["path_length"] == 3
    assert job["path_key"] == pkg["path_key"]


def test_impact_follows_frozen_path_not_full_graph(db_path):
    pkg = _star(db_path, root="N-ODS-ORD", sink="N-DWD-ORD")
    job = impact.start_impact(pkg["root_node_id"], db_path=db_path)
    assert job["path_source"] == "不可变快照"
    assert job["affected_count"] == 2
    hits = impact.job_detail(job["id"], db_path=db_path)["hits"]
    assert [h["node_code"] for h in hits] == ["N-ODS-ORD", "N-DWD-ORD"]


def test_fingerprint_drift_blocks_orchestration_and_impact(db_path):
    pkg = _star(db_path)
    assert paths.orchestrate(pkg["path_key"], "仓管甲", db_path=db_path)["status"] == "已编排"

    # 边关系被改写：作废冻结路径上的一条边
    draft_edge = next(e for e in edges.list_edges(db_path=db_path) if e["edge_code"] == "E-03")
    edges.void_edge(int(draft_edge["id"]), db_path=db_path)

    drifted = paths.get_package(pkg["path_key"], db_path=db_path)
    assert drifted["drift"]["ok"] is False
    blocked = paths.orchestrate(pkg["path_key"], "仓管乙", db_path=db_path)
    assert blocked["status"] == "已拦截"
    assert "拦住" in blocked["message"]
    assert [o["status"] for o in paths.list_orchestrations(db_path=db_path)][0] == "已拦截"

    with pytest.raises(ServiceError) as exc:
        impact.start_impact(pkg["root_node_id"], db_path=db_path)
    assert exc.value.status_code == 409
    assert "已拦住" in exc.value.message


def test_edge_chips_and_node_marks(db_path):
    pkg = _star(db_path)
    edge_items = edges.list_edges(db_path=db_path)
    on_path = [e for e in edge_items if e["path_chips"]]
    assert len(on_path) == 3
    assert all(e["path_chips"][0]["path_key"] == pkg["path_key"] for e in on_path)
    node_items = nodes.list_nodes(db_path=db_path)
    marked = [n for n in node_items if n["is_critical"]]
    assert len(marked) == 4
    assert all(n["path_lengths"] == [3] for n in marked)


def test_concurrent_star_has_single_winner(db_path):
    results: list[object] = []
    barrier = threading.Barrier(2)

    def worker(actor):
        barrier.wait()
        try:
            results.append(("ok", _star(db_path, actor=actor)))
        except ServiceError as exc:
            results.append(("err", exc.message))

    threads = [threading.Thread(target=worker, args=(a,)) for a in ("仓管甲", "仓管乙")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [r for r in results if r[0] == "ok"]
    losers = [r for r in results if r[0] == "err"]
    assert len(winners) == 1
    assert len(losers) == 1
    pkg = paths.list_packages(db_path=db_path)["items"][0]
    assert pkg["starred_by"] in ("仓管甲", "仓管乙")
