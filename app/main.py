import json
from pathlib import Path

from bottle import Bottle, HTTPResponse, request, response, static_file
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import Config
from app.seed import seed_if_needed
from app.services import coverage as coverage_svc
from app.services import edges as edges_svc
from app.services import impact as impact_svc
from app.services import nodes as nodes_svc
from app.services import notify as notify_svc
from app.services import paths as paths_svc
from app.services import settings as settings_svc
from app.services.errors import ServiceError

STATIC = Path(__file__).resolve().parent.parent / "static"
serializer = URLSafeSerializer(Config.SECRET, salt="cc-lineage-session")


def _json(data, status: int = 200):
    body = json.dumps(data, ensure_ascii=False)
    return HTTPResponse(
        body=body,
        status=status,
        headers={"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"},
    )


def _read_json() -> dict:
    raw = request.body.read() if request.body else b""
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ServiceError("JSON 无效") from exc
    return data if isinstance(data, dict) else {}


def _query_text(name: str) -> str:
    """查询串在 WSGI 里按 latin-1 解出，这里还原成 UTF-8 文本。"""
    raw = request.query.get(name) or ""
    try:
        return raw.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw


def _current_user() -> str | None:
    raw = request.get_cookie(Config.COOKIE_NAME)
    if not raw:
        return None
    try:
        data = serializer.loads(raw)
    except BadSignature:
        return None
    if isinstance(data, dict) and data.get("user") == Config.ADMIN_USER:
        return data["user"]
    return None


def _require_user() -> str:
    user = _current_user()
    if not user:
        raise ServiceError("未登录", 401)
    return user


def create_app() -> Bottle:
    seed_if_needed()
    app = Bottle()

    @app.error(500)
    def on_500(error):  # noqa: ARG001
        return _json({"error": "服务器错误"}, 500)

    def wrap(fn):
        def inner(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ServiceError as exc:
                return _json({"error": exc.message}, exc.status_code)

        return inner

    @app.get("/")
    def root():
        if _current_user():
            return static_file("index.html", root=str(STATIC))
        return HTTPResponse(status=302, headers={"Location": "/login"})

    @app.get("/login")
    def login_page():
        return static_file("login.html", root=str(STATIC))

    @app.get("/app")
    @app.get("/app/")
    def spa():
        if not _current_user():
            return HTTPResponse(status=302, headers={"Location": "/login"})
        return static_file("index.html", root=str(STATIC))

    @app.get("/static/<filepath:path>")
    def static_files(filepath):
        return static_file(filepath, root=str(STATIC))

    @app.post("/api/login")
    @wrap
    def api_login():
        body = _read_json()
        user = str(body.get("username") or "")
        password = str(body.get("password") or "")
        if user != Config.ADMIN_USER or password != Config.ADMIN_PASSWORD:
            raise ServiceError("用户名或密码错误", 401)
        token = serializer.dumps({"user": user})
        resp = _json({"ok": True, "user": user})
        resp.set_cookie(
            Config.COOKIE_NAME,
            token,
            path="/",
            httponly=True,
            max_age=86400 * 7,
        )
        return resp

    @app.post("/api/logout")
    @wrap
    def api_logout():
        resp = _json({"ok": True})
        resp.delete_cookie(Config.COOKIE_NAME, path="/")
        return resp

    @app.get("/api/meta")
    @wrap
    def api_meta():
        _require_user()
        return _json(
            {
                "lab": Config.LAB_NAME,
                "user": _current_user(),
                "coverage_chip": coverage_svc.latest_chip(),
            }
        )

    @app.get("/api/nodes")
    @wrap
    def api_nodes_list():
        _require_user()
        layer = request.query.get("layer") or None
        return _json({"items": nodes_svc.list_nodes(layer)})

    @app.post("/api/nodes")
    @wrap
    def api_nodes_create():
        _require_user()
        body = _read_json()
        item = nodes_svc.register_node(
            str(body.get("node_code") or ""),
            str(body.get("table_name") or ""),
            str(body.get("layer") or ""),
            str(body.get("owner_team") or ""),
            str(body.get("note") or ""),
        )
        return _json({"item": item})

    @app.post("/api/nodes/<node_id:int>/activate")
    @wrap
    def api_nodes_activate(node_id):
        _require_user()
        return _json({"item": nodes_svc.activate_node(node_id)})

    @app.post("/api/nodes/<node_id:int>/retire")
    @wrap
    def api_nodes_retire(node_id):
        _require_user()
        return _json({"item": nodes_svc.retire_node(node_id)})

    @app.get("/api/edges")
    @wrap
    def api_edges_list():
        _require_user()
        return _json({"items": edges_svc.list_edges()})

    @app.post("/api/edges")
    @wrap
    def api_edges_create():
        _require_user()
        body = _read_json()
        item = edges_svc.create_edge(
            str(body.get("edge_code") or ""),
            int(body.get("upstream_id") or 0),
            int(body.get("downstream_id") or 0),
            str(body.get("transform_kind") or "直抽"),
            str(body.get("note") or ""),
        )
        return _json({"item": item})

    @app.post("/api/edges/<edge_id:int>/publish")
    @wrap
    def api_edges_publish(edge_id):
        _require_user()
        return _json({"item": edges_svc.publish_edge(edge_id)})

    @app.post("/api/edges/<edge_id:int>/void")
    @wrap
    def api_edges_void(edge_id):
        _require_user()
        return _json({"item": edges_svc.void_edge(edge_id)})

    @app.get("/api/impact/jobs")
    @wrap
    def api_impact_jobs():
        _require_user()
        return _json({"items": impact_svc.list_jobs()})

    @app.get("/api/impact/jobs/<job_id:int>")
    @wrap
    def api_impact_detail(job_id):
        _require_user()
        return _json(impact_svc.job_detail(job_id))

    @app.post("/api/impact/start")
    @wrap
    def api_impact_start():
        _require_user()
        body = _read_json()
        return _json({"job": impact_svc.start_impact(int(body.get("root_node_id") or 0))})

    @app.get("/api/paths")
    @wrap
    def api_paths_list():
        _require_user()
        landing = request.query.get("landing") or None
        return _json(paths_svc.list_packages(landing))

    @app.get("/api/paths/orchestrations")
    @wrap
    def api_paths_orchestrations():
        _require_user()
        return _json({"items": paths_svc.list_orchestrations()})

    @app.get("/api/paths/detail")
    @wrap
    def api_paths_detail():
        _require_user()
        path_key = _query_text("path_key")
        if not path_key:
            raise ServiceError("缺少路径键 path_key")
        landing = request.query.get("landing") or None
        return _json({"item": paths_svc.get_package(path_key, landing)})

    @app.post("/api/paths/star")
    @wrap
    def api_paths_star():
        user = _require_user()
        body = _read_json()
        try:
            root_id = int(body.get("root_node_id") or 0)
            sink_id = int(body.get("sink_node_id") or 0)
        except (TypeError, ValueError) as exc:
            raise ServiceError("起止节点编号无效") from exc
        pkg = paths_svc.star_path(
            root_id,
            sink_id,
            str(body.get("landing") or ""),
            user,
            str(body.get("note") or ""),
        )
        return _json({"item": pkg})

    @app.post("/api/paths/orchestrate")
    @wrap
    def api_paths_orchestrate():
        user = _require_user()
        body = _read_json()
        path_key = str(body.get("path_key") or "").strip()
        if not path_key:
            raise ServiceError("缺少路径键 path_key")
        result = paths_svc.orchestrate(path_key, user)
        if result["status"] != "已编排":
            result["error"] = result["message"]
            return _json(result, 409)
        return _json(result)

    @app.get("/api/notify")
    @wrap
    def api_notify_list():
        _require_user()
        job_id = request.query.get("job_id")
        jid = int(job_id) if job_id else None
        return _json({"items": notify_svc.list_notifies(jid)})

    @app.post("/api/notify/dispatch")
    @wrap
    def api_notify_dispatch():
        _require_user()
        body = _read_json()
        return _json(notify_svc.dispatch_for_job(int(body.get("job_id") or 0)))

    @app.get("/api/coverage/reports")
    @wrap
    def api_coverage_list():
        _require_user()
        return _json({"items": coverage_svc.list_reports()})

    @app.post("/api/coverage/reports")
    @wrap
    def api_coverage_run():
        _require_user()
        return _json({"report": coverage_svc.run_coverage()})

    @app.get("/api/settings")
    @wrap
    def api_settings_list():
        _require_user()
        return _json({"items": settings_svc.list_settings()})

    @app.post("/api/settings")
    @wrap
    def api_settings_update():
        _require_user()
        body = _read_json()
        return _json(
            {
                "item": settings_svc.update_setting(
                    str(body.get("key") or ""),
                    str(body.get("value") or ""),
                )
            }
        )

    return app
