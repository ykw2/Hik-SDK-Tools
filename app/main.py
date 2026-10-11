import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from app.access import client_ip, drop_session, ip_allowed, new_session, valid_session
from app.service import Hub

log = logging.getLogger(__name__)
WEB = Path(__file__).resolve().parent / "web" / "index.html"
ADMIN = Path(__file__).resolve().parent / "web" / "admin.html"
hub = Hub()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    hub.start()
    yield
    hub.stop()


app = FastAPI(title="SDK Endpoint", lifespan=lifespan)


@app.middleware("http")
async def whitelist_gate(request: Request, call_next):
    path = request.url.path
    if path == "/admin" or path.startswith("/api/admin"):
        return await call_next(request)
    if ip_allowed(client_ip(request), hub.config.whitelist()):
        return await call_next(request)
    if path.startswith("/api/"):
        return Response(status_code=404)
    return HTMLResponse("", status_code=200)


def _call(fn):
    try:
        return fn()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _require_admin(request: Request) -> None:
    if not valid_session(request.cookies.get("sdk_admin")):
        raise HTTPException(status_code=401, detail="要先輸入通行碼")


def _passcode_ok(text: str) -> str:
    code = (text or "").strip()
    if len(code) < 4:
        raise ValueError("通行碼至少 4 個字")
    return code


@app.get("/")
def index():
    return FileResponse(WEB, headers={"Cache-Control": "no-cache"})


@app.get("/admin")
def admin_page():
    return FileResponse(ADMIN, headers={"Cache-Control": "no-cache"})


@app.get("/api/admin/bootstrap")
def admin_bootstrap(request: Request):
    return {"passcodeSet": hub.config.has_passcode(), "ip": client_ip(request)}


@app.post("/api/admin/login")
def admin_login(payload: dict):
    code = _call(lambda: _passcode_ok(payload.get("passcode") or ""))
    if not hub.config.has_passcode():
        hub.config.set_passcode(code)
    elif not hub.config.check_passcode(code):
        raise HTTPException(status_code=401, detail="通行碼不對")
    token = new_session()
    response = JSONResponse({"ok": True})
    response.set_cookie("sdk_admin", token, httponly=True, samesite="lax", path="/")
    return response


@app.post("/api/admin/logout")
def admin_logout(request: Request):
    drop_session(request.cookies.get("sdk_admin"))
    response = JSONResponse({"ok": True})
    response.delete_cookie("sdk_admin", path="/")
    return response


@app.get("/api/admin/settings")
def admin_settings(request: Request):
    _require_admin(request)
    return {"whitelist": hub.config.whitelist(), "ip": client_ip(request)}


@app.post("/api/admin/whitelist")
def admin_whitelist(payload: dict, request: Request):
    _require_admin(request)
    text = payload.get("whitelist")
    if isinstance(text, str):
        items = text.splitlines()
    else:
        items = list(text or [])
    saved = _call(lambda: hub.config.set_whitelist(items))
    return {"whitelist": saved}


@app.post("/api/admin/passcode")
def admin_passcode(payload: dict, request: Request):
    _require_admin(request)
    current = payload.get("current") or ""
    nxt = _call(lambda: _passcode_ok(payload.get("next") or ""))
    if not hub.config.check_passcode(current):
        raise HTTPException(status_code=401, detail="目前通行碼不對")
    hub.config.set_passcode(nxt)
    return {"ok": True}


@app.get("/api/status")
def status():
    return hub.status()


@app.get("/api/cameras")
def cameras():
    return hub.cameras()


@app.post("/api/cameras")
def save_camera(payload: dict):
    return _call(lambda: hub.save_camera(payload))


@app.delete("/api/cameras/{camera_id}")
def delete_camera(camera_id: str):
    hub.remove_camera(camera_id)
    return {"ok": True}


@app.post("/api/cameras/{camera_id}/arm")
def arm_camera(camera_id: str):
    return _call(lambda: hub.arm(camera_id))


@app.post("/api/cameras/{camera_id}/disarm")
def disarm_camera(camera_id: str):
    return hub.disarm(camera_id) or {"ok": True}


@app.get("/api/rules")
def rules():
    return hub.rules()


@app.post("/api/rules")
def save_rule(payload: dict):
    return _call(lambda: hub.save_rule(payload))


@app.delete("/api/rules/{rule_id}")
def delete_rule(rule_id: str):
    hub.remove_rule(rule_id)
    return {"ok": True}


@app.post("/api/rules/{rule_id}/test")
def test_rule(rule_id: str):
    return _call(lambda: hub.test_rule(rule_id))


@app.get("/api/events")
def events(limit: int = 50):
    return hub.store.list(max(1, min(limit, 200)))


@app.delete("/api/events")
def clear_events():
    hub.store.clear()
    return {"ok": True}


@app.delete("/api/events/{event_id}")
def delete_event(event_id: str):
    if not hub.store.delete(event_id):
        raise HTTPException(status_code=404, detail="找不到這筆紀錄")
    return {"ok": True}


@app.get("/api/forward-logs")
def forward_logs(limit: int = 50):
    return hub.store.logs(max(1, min(limit, 200)))


@app.post("/api/simulate")
def simulate(payload: dict | None = None):
    payload = payload or {}
    return hub.simulate(payload.get("plate") or "粵B12345", int(payload.get("lane") or 1))


@app.get("/api/shots/{name}")
def shot(name: str):
    path = hub.store.shot_path(name)
    if path is None:
        raise HTTPException(status_code=404, detail="找不到圖片")
    return FileResponse(path, media_type="image/jpeg")


def main():
    import uvicorn

    port = int(os.environ.get("APP_PORT", "8123"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
