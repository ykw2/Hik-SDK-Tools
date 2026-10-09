import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.service import Hub

log = logging.getLogger(__name__)
WEB = Path(__file__).resolve().parent / "web" / "index.html"
hub = Hub()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    hub.start()
    yield
    hub.stop()


app = FastAPI(title="卡口車牌", lifespan=lifespan)


def _call(fn):
    try:
        return fn()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/")
def index():
    return FileResponse(WEB, headers={"Cache-Control": "no-cache"})


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
