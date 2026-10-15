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
from app.parser import parse_access, parse_alarm
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


class AccessParseTests(unittest.TestCase):
    def test_face_pass_reads_person_time_and_image(self):
        from ctypes import Structure, c_uint8, c_uint16, c_uint32, c_void_p

        class Event(Structure):
            _pack_ = 4
            _fields_ = [
                ("dwSize", c_uint32),
                ("byCardNo", c_uint8 * 32),
                ("byCardType", c_uint8),
                ("byAllowListNo", c_uint8),
                ("byReportChannel", c_uint8),
                ("byCardReaderKind", c_uint8),
                ("dwCardReaderNo", c_uint32),
                ("dwDoorNo", c_uint32),
                ("dwVerifyNo", c_uint32),
                ("dwAlarmInNo", c_uint32),
                ("dwAlarmOutNo", c_uint32),
                ("dwCaseSensorNo", c_uint32),
                ("dwRs485No", c_uint32),
                ("dwMultiCardGroupNo", c_uint32),
                ("wAccessChannel", c_uint16),
                ("byDeviceNo", c_uint8),
                ("byDistractControlNo", c_uint8),
                ("dwEmployeeNo", c_uint32),
                ("wLocalControllerID", c_uint16),
                ("byInternetAccess", c_uint8),
                ("byType", c_uint8),
                ("byMACAddr", c_uint8 * 6),
                ("bySwipeCardType", c_uint8),
                ("byMask", c_uint8),
                ("dwSerialNo", c_uint32),
                ("byChannelControllerID", c_uint8),
                ("byChannelControllerLampID", c_uint8),
                ("byChannelControllerIRAdaptorID", c_uint8),
                ("byChannelControllerIREmitterID", c_uint8),
                ("byRes", c_uint8 * 4),
            ]

        class Alarm(Structure):
            _pack_ = 4
            _fields_ = [
                ("dwSize", c_uint32),
                ("dwMajor", c_uint32),
                ("dwMinor", c_uint32),
                ("dwYear", c_uint32),
                ("dwMonth", c_uint32),
                ("dwDay", c_uint32),
                ("dwHour", c_uint32),
                ("dwMinute", c_uint32),
                ("dwSecond", c_uint32),
                ("sNetUser", c_uint8 * 16),
                ("sIpV4", c_uint8 * 16),
                ("byIPv6", c_uint8 * 128),
                ("event", Event),
                ("dwPicDataLen", c_uint32),
                ("pPicData", c_void_p),
            ]

        jpeg = b"\xff\xd8\xff\xd9face"
        image = ctypes.create_string_buffer(jpeg)
        obj = Alarm()
        obj.dwSize = ctypes.sizeof(Alarm)
        obj.dwMajor = 5
        obj.dwMinor = 0x4B
        obj.dwYear = 2026
        obj.dwMonth = 10
        obj.dwDay = 15
        obj.dwHour = 21
        obj.dwMinute = 40
        obj.dwSecond = 8
        obj.event.dwDoorNo = 1
        obj.event.dwEmployeeNo = 88
        for index, value in enumerate(b"0012345678"):
            obj.event.byCardNo[index] = value
        obj.dwPicDataLen = len(jpeg)
        obj.pPicData = ctypes.addressof(image)
        parsed = parse_access(ctypes.addressof(obj), ctypes.sizeof(obj))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["method"], "人臉認證通過")
        self.assertEqual(parsed["employeeNo"], "88")
        self.assertEqual(parsed["cardNo"], "0012345678")
        self.assertEqual(parsed["doorNo"], 1)
        self.assertEqual(parsed["time"], "2026-10-15T21:40:08+08:00")
        self.assertEqual(parsed["imageBytes"], jpeg)
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
            self.assertIn("裝置", page.text)
            self.assertIn("發布規則", page.text)
            self.assertIn("清除全部", page.text)
            self.assertIn("API 原始內容", page.text)
            self.assertIn("人面門禁", page.text)
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
            gone = client.delete("/api/events/" + body["id"])
            self.assertEqual(gone.status_code, 200, gone.text)
            self.assertEqual(client.get("/api/events").json(), [])
            again = client.post("/api/simulate", json={"plate": "粵B99999", "lane": 2})
            self.assertEqual(again.status_code, 200, again.text)
            cleared = client.delete("/api/events")
            self.assertEqual(cleared.status_code, 200, cleared.text)
            self.assertEqual(client.get("/api/events").json(), [])

    def test_event_keeps_every_image_and_push_body(self):
        from app.main import hub

        received = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                received["body"] = json.loads(self.rfile.read(length).decode("utf-8"))
                self.send_response(200)
                self.end_headers()

            def log_message(self, _fmt, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        rule_id = ""
        event = None
        try:
            port = server.server_address[1]
            rule = hub.config.upsert_rule(
                {
                    "name": "原始內容",
                    "url": f"http://127.0.0.1:{port}/plate",
                    "method": "POST",
                    "enabled": True,
                    "headers": {},
                    "template": {"license": "{{plate}}", "laneNo": "{{lane}}"},
                }
            )
            rule_id = rule["id"]
            event = hub._store_plate(
                {
                    "plate": "粵Z00001",
                    "lane": 3,
                    "images": [
                        {"kind": 1, "bytes": b"\xff\xd8scene"},
                        {"kind": 0, "bytes": b"\xff\xd8plate"},
                        {"kind": 2, "bytes": b"\xff\xd8merge"},
                        {"kind": 1, "url": "http://192.168.1.64/pic.jpg"},
                    ],
                }
            )
            self.assertEqual([item["label"] for item in event["images"]], ["場景", "車牌", "合成", "場景 2"])
            for item in event["images"][:3]:
                self.assertTrue((hub.store.shots / item["file"]).is_file())
            hub._forward(event)
            self.assertEqual(event["pushes"][0]["body"], {"license": "粵Z00001", "laneNo": 3})
            self.assertEqual(received["body"], {"license": "粵Z00001", "laneNo": 3})
            listed = next(item for item in hub.store.list() if item["id"] == event["id"])
            self.assertEqual(listed["pushes"][0]["body"]["license"], "粵Z00001")
        finally:
            server.shutdown()
            if rule_id:
                hub.config.delete_rule(rule_id)
            if event:
                names = [item.get("file") for item in event["images"]]
                hub.store.delete(event["id"])
                for name in names:
                    if name:
                        self.assertFalse((hub.store.shots / name).exists())


class AccessTests(unittest.TestCase):
    def test_whitelist_blanks_other_ips_and_passcode_changes(self):
        with TestClient(app) as client:
            open_page = client.get("/")
            self.assertIn("SDK Endpoint", open_page.text)
            blocked_login = client.post("/api/admin/login", json={"passcode": "no"})
            self.assertEqual(blocked_login.status_code, 400)
            first = client.post("/api/admin/login", json={"passcode": "open-1234"})
            self.assertEqual(first.status_code, 200, first.text)
            saved = client.post("/api/admin/whitelist", json={"whitelist": "10.1.2.3\n192.168.77.0/24"})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["whitelist"], ["10.1.2.3", "192.168.77.0/24"])
            blank = client.get("/")
            self.assertEqual(blank.status_code, 200)
            self.assertEqual(blank.text, "")
            self.assertEqual(client.get("/api/events").status_code, 404)
            admin = client.get("/admin")
            self.assertEqual(admin.status_code, 200)
            self.assertIn("白名單", admin.text)
            changed = client.post("/api/admin/passcode", json={"current": "open-1234", "next": "next-5678"})
            self.assertEqual(changed.status_code, 200, changed.text)
            client.post("/api/admin/logout")
            wrong = client.post("/api/admin/login", json={"passcode": "open-1234"})
            self.assertEqual(wrong.status_code, 401)
            ok = client.post("/api/admin/login", json={"passcode": "next-5678"})
            self.assertEqual(ok.status_code, 200, ok.text)
            client.post("/api/admin/whitelist", json={"whitelist": ""})


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

    def test_alarm_is_kept_on_that_camera(self):
        from app.service import Hub

        os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="hik-alarm-")
        hub = Hub()
        saved = hub.config.upsert_camera(
            {
                "name": "cctv",
                "host": "192.168.38.202",
                "port": 8000,
                "username": "admin",
                "password": "x",
            }
        )
        hub.sessions[saved["id"]] = {"status": "armed", "userId": 4, "handle": 1}
        hub._by_user[4] = saved["id"]
        hub._handle_alarm({
            "commandOnly": 0x4000,
            "userId": 4,
            "deviceIp": "192.168.38.202",
            "detail": {"報警類型": "移動偵測", "輸入號": 1},
        })
        view = hub.cameras()[0]
        self.assertEqual(view["lastCommand"], "0x4000")
        self.assertEqual(view["lastDetail"]["報警類型"], "移動偵測")
        self.assertTrue(view["lastCommandAt"].endswith("+08:00"))

    def test_alarm_v30_summary(self):
        from app.parser import summarize_command

        buf = (ctypes.c_uint32 * 2)(3, 2)
        detail = summarize_command(0x4000, ctypes.addressof(buf), ctypes.sizeof(buf))
        self.assertEqual(detail["報警類型"], "移動偵測")
        self.assertEqual(detail["輸入號"], 2)


if __name__ == "__main__":
    unittest.main()
