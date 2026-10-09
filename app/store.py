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
