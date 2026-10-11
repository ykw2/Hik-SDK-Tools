"""車牌事件與轉發紀錄。網頁看的是這裡的原始內容。"""

from __future__ import annotations

import json
import threading
from collections import deque
from pathlib import Path


class EventStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.shots = self.data_dir / "shots"
        self.shots.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "events.jsonl"
        self._lock = threading.Lock()
        self._events = deque(maxlen=300)
        self._logs = deque(maxlen=300)
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        lines = self.path.read_text(encoding="utf-8").splitlines()
        if len(lines) > 2000:
            lines = lines[-500:]
            self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        for line in reversed(lines[-300:]):
            line = line.strip()
            if not line:
                continue
            try:
                self._events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    def add(self, event: dict):
        with self._lock:
            self._events.appendleft(event)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def list(self, limit: int = 50) -> list:
        with self._lock:
            return list(self._events)[:limit]

    def latest(self) -> dict | None:
        with self._lock:
            return self._events[0] if self._events else None

    def delete(self, event_id: str) -> bool:
        with self._lock:
            found = next((item for item in self._events if item.get("id") == event_id), None)
            if found is None:
                found = self._find_in_file(event_id)
            if found is None:
                return False
            self._events = deque(
                (item for item in self._events if item.get("id") != event_id),
                maxlen=self._events.maxlen,
            )
            self._remove_images(found)
            self._rewrite_file({event_id})
            return True

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self.path.write_text("", encoding="utf-8")
            for image in self.shots.glob("*.jpg"):
                image.unlink(missing_ok=True)

    def _find_in_file(self, event_id: str) -> dict | None:
        if not self.path.exists():
            return None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("id") == event_id:
                return event
        return None

    def _remove_images(self, event: dict) -> None:
        for key in ("sceneImage", "plateImage"):
            name = str(event.get(key) or "")
            if not name:
                continue
            target = self.shots / Path(name).name
            if target.is_file():
                target.unlink()

    def _rewrite_file(self, drop_ids: set[str]) -> None:
        lines = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                text = line.strip()
                if not text:
                    continue
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if event.get("id") in drop_ids:
                    continue
                lines.append(text)
        self.path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")

    def save_image(self, event_id: str, role: str, blob: bytes) -> str:
        name = f"{event_id}_{role}.jpg"
        target = self.shots / name
        target.write_bytes(blob)
        return name

    def shot_path(self, name: str) -> Path | None:
        safe = Path(name).name
        if safe != name or not safe.endswith(".jpg"):
            return None
        target = self.shots / safe
        if not target.is_file():
            return None
        return target

    def add_log(self, entry: dict):
        with self._lock:
            self._logs.appendleft(entry)

    def logs(self, limit: int = 50) -> list:
        with self._lock:
            return list(self._logs)[:limit]
