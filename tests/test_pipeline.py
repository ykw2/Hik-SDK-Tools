"""車牌拆包、JSON 模板、轉發與網頁 API。"""

import json
import os
import sys
import tempfile
import threading
import unittest
import ctypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="hik-data-")
os.environ["HIKSDK_PATH"] = tempfile.mkdtemp(prefix="hik-sdk-")

from app.forwarder import Forwarder, render_template
from app.parser import parse_alarm
from app.sdk_structs import COMM_ITS_PLATE_RESULT, get_structs
from fastapi.testclient import TestClient
from app.main import app


def _put_bytes(array, blob: bytes):
    for index, value in enumerate(blob[: len(array)]):
        array[index] = value


def _build_its(name: str, pack: int):
    cls = get_structs(pack)[name]
    obj = cls()
    obj.dwSize = ctypes.sizeof(cls)
    obj.byDriveChan = 2
    obj.byDir = 2
    obj.byDetectType = 1
    if hasattr(obj, "byVehicleType"):
        obj.byVehicleType = 3
    if hasattr(obj, "byCarDirectionType"):
        obj.byCarDirectionType = 1
    obj.struPlateInfo.byColor = 0
    obj.struPlateInfo.byEntireBelieve = 90
    _put_bytes(obj.struPlateInfo.sLicense, "蓝粤B12345".encode("gbk"))
    obj.struVehicleInfo.wSpeed = 21
    obj.struVehicleInfo.byColor = 1
    jpeg = b"\xff\xd8\xff\xd9scene"
    image = ctypes.create_string_buffer(jpeg)
    obj.dwPicNum = 1
    obj.struPicInfo[0].dwDataLen = len(jpeg)
    obj.struPicInfo[0].byType = 1
    obj.struPicInfo[0].pBuffer = ctypes.addressof(image)
    _put_bytes(obj.struPicInfo[0].byAbsTime, b"20261009012230111")
    return obj, image


class ParseTests(unittest.TestCase):
    def test_v1_and_v2_roundtrip(self):
        for name in ("ItsV0", "ItsV1", "ItsV3", "ItsV2"):
            for pack in (4, 1, None):
                with self.subTest(name=name, pack=pack):
                    obj, image = _build_its(name, pack)
                    event = parse_alarm(
                        COMM_ITS_PLATE_RESULT,
                        ctypes.addressof(obj),
                        ctypes.sizeof(obj),
                    )
                    self.assertIsNotNone(event)
                    self.assertEqual(event["plate"], "粤B12345")
                    self.assertEqual(event["plateColor"], "藍色")
                    self.assertEqual(event["lane"], 2)
                    self.assertEqual(event["direction"], "下行")
                    self.assertEqual(event["speed"], 21)
                    self.assertTrue(event["images"])
                    self.assertEqual(event["images"][0]["bytes"][:2], b"\xff\xd8")
                    self.assertTrue(event["time"].startswith("2026-10-09T01:22:30"))
                    self.assertTrue(event["time"].endswith("+08:00"))
                    del image


class TemplateTests(unittest.TestCase):
    def test_replace_and_keep_literal(self):
        body = render_template(
            {"license": "{{plate}}", "gate": "入口1", "laneNo": "{{lane}}", "note": "車 {{plate}}"},
            {"plate": "粵B12345", "lane": 1},
        )
        self.assertEqual(
            body,
            {"license": "粵B12345", "gate": "入口1", "laneNo": 1, "note": "車 粵B12345"},
        )


class ForwardTests(unittest.TestCase):
    def test_post_modified_json(self):
        received = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                received["body"] = json.loads(self.rfile.read(length).decode("utf-8"))
                received["auth"] = self.headers.get("Authorization")
                self.send_response(200)
                self.end_headers()

            def log_message(self, _fmt, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            result = Forwarder().send(
                {
                    "id": "r1",
                    "name": "測試",
                    "url": f"http://127.0.0.1:{port}/plate",
                    "method": "POST",
                    "headers": {"Authorization": "Bearer abc"},
                    "template": {"license": "{{plate}}", "gate": "入口1"},
                    "timeoutSec": 3,
                },
                {"id": "e1", "plate": "粵B12345"},
            )
        finally:
            server.shutdown()
        self.assertTrue(result["ok"])
        self.assertEqual(received["body"], {"license": "粵B12345", "gate": "入口1"})
        self.assertEqual(received["auth"], "Bearer abc")


class ApiTests(unittest.TestCase):
    def test_simulate_and_rule(self):
        with TestClient(app) as client:
            saved = client.post(
                "/api/rules",
                json={
                    "name": "閘口",
                    "url": "http://127.0.0.1:9/missing",
                    "method": "POST",
                    "enabled": False,
                    "headers": {},
                    "template": {"license": "{{plate}}", "gate": "入口1"},
                },
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            sim = client.post("/api/simulate", json={"plate": "粵B12345", "lane": 1})
            self.assertEqual(sim.status_code, 200, sim.text)
            body = sim.json()
            self.assertEqual(body["plate"], "粵B12345")
            self.assertEqual(body["lane"], 1)
            listed = client.get("/api/events")
            self.assertEqual(listed.json()[0]["plate"], "粵B12345")
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("SDK Endpoint", page.text)
            self.assertIn("發布規則", page.text)
            self.assertNotIn("卡口車牌", page.text)
            self.assertNotIn("布防收出入口結果", page.text)
            self.assertIn("上線", page.text)
            first = client.post(
                "/api/cameras",
                json={"name": "入口", "host": "192.168.38.14", "port": 8000, "username": "admin", "password": "a"},
            )
            second = client.post(
                "/api/cameras",
                json={"name": "face", "host": "192.168.77.230", "port": 8000, "username": "admin", "password": "b"},
            )
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(second.status_code, 200, second.text)
            third = client.post(
                "/api/cameras",
                json={
                    "create": True,
                    "id": second.json()["id"],
                    "name": "第三台",
                    "host": "192.168.77.37",
                    "port": 8000,
                    "username": "admin",
                    "password": "c",
                },
            )
            self.assertEqual(third.status_code, 200, third.text)
            hosts = [item["host"] for item in client.get("/api/cameras").json()]
            self.assertEqual(hosts.count("192.168.38.14"), 1)
            self.assertEqual(hosts.count("192.168.77.230"), 1)
            self.assertEqual(hosts.count("192.168.77.37"), 1)
            self.assertNotEqual(third.json()["id"], second.json()["id"])


class OnlineTests(unittest.TestCase):
    def test_heartbeat_exception_marks_offline(self):
        from app.service import Hub, online_from_exception

        self.assertEqual(online_from_exception(0x8000), "offline")
        self.assertEqual(online_from_exception(0x8006), "reconnecting")
        self.assertEqual(online_from_exception(0x8041), "online")
        self.assertIsNone(online_from_exception(0x8003))
        os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="hik-online-")
        hub = Hub()
        saved = hub.config.upsert_camera(
            {
                "name": "卡口",
                "host": "10.0.0.8",
                "port": 8000,
                "username": "admin",
                "password": "x",
            }
        )
        hub.sessions[saved["id"]] = {
            "status": "armed",
            "online": "online",
            "checkedAt": "2026-10-09T09:00:00",
            "error": "",
            "userId": 3,
            "handle": 1,
        }
        hub._by_user[3] = saved["id"]
        hub._apply_exception(0x8000, 3)
        view = hub.cameras()[0]
        self.assertEqual(view["status"], "armed")
        self.assertEqual(view["online"], "offline")
        self.assertNotEqual(view["checkedAt"], "2026-10-09T09:00:00")


if __name__ == "__main__":
    unittest.main()
