"""布防、存車牌、套模板再轉發。SDK 回調只負責把事件放進佇列。"""

from __future__ import annotations

import logging
import os
import queue
import threading
import uuid
from datetime import datetime
from pathlib import Path

from app.config_store import ConfigStore
from app.forwarder import Forwarder, parse_headers, parse_template
from app.sdk_client import SdkClient
from app.sdk_structs import all_layouts
from app.store import EventStore

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]

REARM_TYPES = {0x8000, 0x8003, 0x8017}


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sample_event() -> dict:
    return {
        "id": "sample",
        "time": now_text(),
        "cameraId": "",
        "cameraName": "入口1",
        "device": "192.168.1.64",
        "plate": "粵B12345",
        "plateRaw": "蓝粤B12345",
        "plateColor": "藍色",
        "plateType": "標準民用",
        "confidence": 96,
        "lane": 1,
        "direction": "下行",
        "carDirection": "從上往下",
        "vehicleType": "轎車",
        "vehicleColor": "白色",
        "speed": 18,
        "detectType": "地感觸發",
        "monitoringSite": "",
        "deviceNo": "",
        "sceneImage": "",
        "plateImage": "",
        "simulated": True,
    }


class Hub:
    def __init__(self):
        self.sdk_path = Path(os.environ.get("HIKSDK_PATH", ROOT / "sdk"))
        self.data_dir = Path(os.environ.get("DATA_DIR", ROOT / "data"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config = ConfigStore(self.data_dir / "config.json")
        self.store = EventStore(self.data_dir)
        self.forwarder = Forwarder()
        self.sdk = SdkClient(self._on_alarm, self._on_exception)
        self.sessions: dict[str, dict] = {}
        self._by_user: dict[int, str] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue(maxsize=2000)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_command = ""
        self._rearm_at: dict[str, float] = {}

    def start(self):
        self.sdk.init(self.sdk_path, self.data_dir / "sdklog")
        self._thread = threading.Thread(target=self._loop, name="plate-worker", daemon=True)
        self._thread.start()
        for camera in self.config.cameras():
            if camera.get("autoArm"):
                try:
                    self.arm(camera["id"])
                except Exception as exc:
                    log.warning("自動布防 %s 失敗：%s", camera.get("host"), exc)

    def stop(self):
        self._stop.set()
        for camera in self.config.cameras():
            try:
                self.disarm(camera["id"])
            except Exception:
                log.exception("停止時撤防失敗")
        self.sdk.cleanup()

    def status(self) -> dict:
        armed = sum(1 for item in self.sessions.values() if item.get("status") == "armed")
        return {
            "sdkLoaded": self.sdk.loaded,
            "sdkError": self.sdk.error,
            "sdkPath": str(self.sdk_path),
            "armed": armed,
            "lastCommand": self.last_command,
            "layouts": [
                {"name": item["name"], "pack": item["pack"], "size": item["size"]}
                for item in all_layouts()
            ],
        }

    def cameras(self) -> list:
        return [self._camera_view(item) for item in self.config.cameras()]

    def save_camera(self, payload: dict) -> dict:
        was_armed = False
        camera_id = payload.get("id")
        if camera_id and self.sessions.get(camera_id, {}).get("status") == "armed":
            was_armed = True
            self.disarm(camera_id)
        saved = self.config.upsert_camera(payload)
        if was_armed or (not camera_id and saved.get("autoArm")):
            try:
                self.arm(saved["id"])
            except RuntimeError:
                return self._camera_view(self.config.camera(saved["id"]))
        return self._camera_view(self.config.camera(saved["id"]))

    def remove_camera(self, camera_id: str):
        self.disarm(camera_id)
        self.config.delete_camera(camera_id)

    def arm(self, camera_id: str) -> dict:
        camera = self.config.camera(camera_id)
        if not camera:
            raise ValueError("找不到鏡頭")
        if not self.sdk.loaded:
            raise RuntimeError(self.sdk.error or "SDK 尚未載入")
        with self._lock:
            self._disarm_locked(camera_id)
            user_id = self.sdk.login(camera["host"], int(camera["port"]), camera["username"], camera["password"])
            if user_id < 0:
                message = self.sdk.last_error()
                self.sessions[camera_id] = {"status": "error", "error": message, "userId": -1, "handle": -1}
                raise RuntimeError(message)
            handle = self.sdk.arm(user_id)
            if handle < 0:
                message = self.sdk.last_error()
                self.sdk.logout(user_id)
                self.sessions[camera_id] = {"status": "error", "error": message, "userId": -1, "handle": -1}
                raise RuntimeError(message)
            self.sessions[camera_id] = {
                "status": "armed",
                "error": "",
                "userId": user_id,
                "handle": handle,
            }
            self._by_user[user_id] = camera_id
        return self._camera_view(camera)

    def disarm(self, camera_id: str) -> dict | None:
        with self._lock:
            self._disarm_locked(camera_id)
        camera = self.config.camera(camera_id)
        return self._camera_view(camera) if camera else None

    def rules(self) -> list:
        return self.config.rules()

    def save_rule(self, payload: dict) -> dict:
        payload = dict(payload)
        payload["template"] = parse_template(payload.get("template"))
        payload["headers"] = parse_headers(payload.get("headers"))
        return self.config.upsert_rule(payload)

    def remove_rule(self, rule_id: str):
        self.config.delete_rule(rule_id)

    def test_rule(self, rule_id: str) -> dict:
        rule = self.config.rule(rule_id)
        if not rule:
            raise ValueError("找不到發布規則")
        event = self.store.latest() or sample_event()
        result = self.forwarder.send(rule, event)
        result["time"] = now_text()
        self.store.add_log(result)
        return result

    def simulate(self, plate: str = "粵B12345", lane: int = 1) -> dict:
        parsed = {
            "plate": plate or "粵B12345",
            "plateRaw": plate or "粵B12345",
            "plateColor": "藍色",
            "plateType": "標準民用",
            "confidence": 96,
            "lane": int(lane or 1),
            "direction": "下行",
            "carDirection": "從上往下",
            "vehicleType": "轎車",
            "vehicleColor": "白色",
            "speed": 18,
            "detectType": "地感觸發",
            "time": now_text(),
            "deviceIp": "",
            "userId": -1,
            "simulated": True,
            "images": [],
        }
        event = self._store_plate(parsed)
        self._forward(event)
        return event

    def _camera_view(self, camera: dict | None) -> dict:
        if not camera:
            return {}
        session = self.sessions.get(camera["id"], {})
        return {
            "id": camera["id"],
            "name": camera["name"],
            "host": camera["host"],
            "port": camera["port"],
            "username": camera["username"],
            "autoArm": bool(camera.get("autoArm")),
            "status": session.get("status", "idle"),
            "error": session.get("error", ""),
        }

    def _disarm_locked(self, camera_id: str):
        session = self.sessions.pop(camera_id, None)
        if not session:
            return
        user_id = session.get("userId", -1)
        self._by_user.pop(user_id, None)
        try:
            self.sdk.disarm(session.get("handle", -1))
            self.sdk.logout(user_id)
        except Exception:
            log.exception("撤防失敗")

    def _on_alarm(self, payload: dict):
        try:
            self._queue.put_nowait(("alarm", payload))
        except queue.Full:
            log.warning("車牌佇列已滿，丟棄一筆")

    def _on_exception(self, typ: int, user_id: int):
        if typ not in REARM_TYPES:
            return
        try:
            self._queue.put_nowait(("rearm", user_id))
        except queue.Full:
            pass

    def _loop(self):
        while not self._stop.is_set():
            try:
                kind, payload = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if kind == "alarm":
                    self._handle_alarm(payload)
                elif kind == "rearm":
                    self._rearm(payload)
            except Exception:
                log.exception("處理佇列失敗")

    def _handle_alarm(self, payload: dict):
        if payload.get("commandOnly"):
            self.last_command = f"0x{int(payload['commandOnly']):04X}"
            return
        command = payload.get("command")
        if isinstance(command, int):
            self.last_command = f"0x{command:04X}"
        event = self._store_plate(payload)
        self._forward(event)

    def _store_plate(self, payload: dict) -> dict:
        camera = self._camera_for(payload.get("userId"))
        event_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        scene = ""
        plate_image = ""
        for image in payload.get("images") or []:
            if image.get("url"):
                payload["sceneImageUrl"] = image["url"]
                continue
            blob = image.get("bytes")
            if not blob:
                continue
            role = {0: "plate", 1: "scene", 2: "merge"}.get(image.get("kind"), f"pic{image.get('kind')}")
            name = self.store.save_image(event_id, role, blob)
            if role == "plate" and not plate_image:
                plate_image = name
            elif role == "scene" and not scene:
                scene = name
            elif role == "merge" and not scene:
                scene = name
        device = payload.get("deviceIp") or (camera or {}).get("host") or ""
        event = {
            "id": event_id,
            "time": payload.get("time") or now_text(),
            "cameraId": (camera or {}).get("id", ""),
            "cameraName": (camera or {}).get("name", ""),
            "device": device,
            "plate": payload.get("plate") or "",
            "plateRaw": payload.get("plateRaw") or "",
            "plateColor": payload.get("plateColor") or "",
            "plateType": payload.get("plateType") or "",
            "confidence": payload.get("confidence") or 0,
            "lane": payload.get("lane") or 0,
            "direction": payload.get("direction") or "",
            "carDirection": payload.get("carDirection") or "",
            "vehicleType": payload.get("vehicleType") or "",
            "vehicleColor": payload.get("vehicleColor") or "",
            "speed": payload.get("speed") or 0,
            "detectType": payload.get("detectType") or "",
            "monitoringSite": payload.get("monitoringSite") or "",
            "deviceNo": payload.get("deviceNo") or "",
            "sceneImage": scene,
            "plateImage": plate_image,
            "sceneImageUrl": payload.get("sceneImageUrl") or "",
            "layout": payload.get("layout") or "",
            "structSize": payload.get("structSize") or 0,
            "reportedSize": payload.get("reportedSize") or 0,
            "parseWarning": payload.get("parseWarning") or "",
            "simulated": bool(payload.get("simulated")),
            "receivedAt": now_text(),
        }
        self.store.add(event)
        return event

    def _forward(self, event: dict):
        for rule in self.config.rules():
            if not rule.get("enabled", True):
                continue
            result = self.forwarder.send(rule, event)
            result["time"] = now_text()
            self.store.add_log(result)

    def _camera_for(self, user_id) -> dict | None:
        if user_id is None or int(user_id) < 0:
            return None
        camera_id = self._by_user.get(int(user_id))
        if not camera_id:
            return None
        return self.config.camera(camera_id)

    def _rearm(self, user_id: int):
        camera_id = self._by_user.get(int(user_id))
        if not camera_id:
            return
        stamp = datetime.now().timestamp()
        if stamp - self._rearm_at.get(camera_id, 0) < 5:
            return
        self._rearm_at[camera_id] = stamp
        log.warning("鏡頭連線中斷，重新布防 %s", camera_id)
        try:
            self.arm(camera_id)
        except Exception as exc:
            log.warning("重新布防失敗：%s", exc)
