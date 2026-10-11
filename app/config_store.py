"""鏡頭、發布規則、白名單與通行碼存在 data/config.json。"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import threading
import uuid
from pathlib import Path


class ConfigStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data = {"cameras": [], "rules": [], "whitelist": [], "passcodeHash": ""}
        self.load()

    def load(self):
        if not self.path.exists():
            self.save()
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        self._data = {
            "cameras": list(data.get("cameras") or []),
            "rules": list(data.get("rules") or []),
            "whitelist": [str(item) for item in (data.get("whitelist") or [])],
            "passcodeHash": str(data.get("passcodeHash") or ""),
        }

    def save(self):
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def cameras(self) -> list:
        with self._lock:
            return [dict(item) for item in self._data["cameras"]]

    def camera(self, camera_id: str) -> dict | None:
        with self._lock:
            for item in self._data["cameras"]:
                if item["id"] == camera_id:
                    return dict(item)
        return None

    def upsert_camera(self, payload: dict) -> dict:
        with self._lock:
            camera_id = payload.get("id") or uuid.uuid4().hex
            current = next((item for item in self._data["cameras"] if item["id"] == camera_id), None)
            password = payload.get("password")
            if current and password in ("", None, "******"):
                password = current.get("password", "")
            item = {
                "id": camera_id,
                "name": (payload.get("name") or "卡口").strip(),
                "host": (payload.get("host") or "").strip(),
                "port": int(payload.get("port") or 8000),
                "username": (payload.get("username") or "admin").strip(),
                "password": password or "",
                "autoArm": bool(payload.get("autoArm")),
            }
            if not item["host"]:
                raise ValueError("要填裝置 IP")
            if current:
                current.update(item)
                stored = current
            else:
                self._data["cameras"].append(item)
                stored = item
            self.save()
            return dict(stored)

    def delete_camera(self, camera_id: str) -> None:
        with self._lock:
            self._data["cameras"] = [item for item in self._data["cameras"] if item["id"] != camera_id]
            self.save()

    def rules(self) -> list:
        with self._lock:
            return [dict(item) for item in self._data["rules"]]

    def rule(self, rule_id: str) -> dict | None:
        with self._lock:
            for item in self._data["rules"]:
                if item["id"] == rule_id:
                    return dict(item)
        return None

    def upsert_rule(self, payload: dict) -> dict:
        with self._lock:
            rule_id = payload.get("id") or uuid.uuid4().hex
            item = {
                "id": rule_id,
                "name": (payload.get("name") or "發布").strip(),
                "enabled": bool(payload.get("enabled", True)),
                "url": (payload.get("url") or "").strip(),
                "method": (payload.get("method") or "POST").upper(),
                "headers": payload.get("headers") or {},
                "timeoutSec": float(payload.get("timeoutSec") or 5),
                "template": payload.get("template") or {},
            }
            if item["method"] not in ("POST", "PUT"):
                raise ValueError("方法只接受 POST 或 PUT")
            current = next((row for row in self._data["rules"] if row["id"] == rule_id), None)
            if current:
                current.update(item)
                stored = current
            else:
                self._data["rules"].append(item)
                stored = item
            self.save()
            return dict(stored)

    def delete_rule(self, rule_id: str) -> None:
        with self._lock:
            self._data["rules"] = [item for item in self._data["rules"] if item["id"] != rule_id]
            self.save()

    def whitelist(self) -> list[str]:
        with self._lock:
            return list(self._data.get("whitelist") or [])

    def set_whitelist(self, items: list[str]) -> list[str]:
        cleaned = normalize_whitelist(items)
        with self._lock:
            self._data["whitelist"] = cleaned
            self.save()
            return list(cleaned)

    def has_passcode(self) -> bool:
        with self._lock:
            return bool(self._data.get("passcodeHash"))

    def set_passcode(self, passcode: str) -> None:
        digest = hash_passcode(passcode)
        with self._lock:
            self._data["passcodeHash"] = digest
            self.save()

    def check_passcode(self, passcode: str) -> bool:
        with self._lock:
            stored = self._data.get("passcodeHash") or ""
        if not stored:
            return False
        return hmac.compare_digest(stored, hash_passcode(passcode))


def hash_passcode(passcode: str) -> str:
    return hashlib.sha256(passcode.encode("utf-8")).hexdigest()


def normalize_whitelist(items: list[str]) -> list[str]:
    cleaned = []
    seen = set()
    for raw in items:
        for piece in str(raw).replace(",", "\n").splitlines():
            text = piece.strip()
            if not text or text in seen:
                continue
            if "/" in text:
                ipaddress.ip_network(text, strict=False)
            else:
                ipaddress.ip_address(text)
            seen.add(text)
            cleaned.append(text)
    return cleaned
