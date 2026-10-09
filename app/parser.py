"""把卡口報警緩衝拆成車牌事件。只在結構大小吻合時才跟隨圖片指標。"""

from __future__ import annotations

import ctypes
import re
from ctypes import c_int32, c_uint32

from app.sdk_structs import COMM_ITS_PLATE_RESULT, COMM_UPLOAD_PLATE_RESULT, all_layouts, get_structs

PLATE_COLORS = {
    0: "藍色",
    1: "黃色",
    2: "白色",
    3: "黑色",
    4: "綠色",
    5: "民航黑",
    6: "民航綠",
}
PLATE_TYPES = {
    0: "標準民用",
    1: "02式民用",
    2: "武警",
    3: "警車",
    4: "雙行尾牌",
    5: "使館",
    6: "農用車",
    7: "摩托車",
}
DIRECTIONS = {
    1: "上行",
    2: "下行",
    3: "雙向",
    4: "由東向西",
    5: "由南向北",
    6: "由西向東",
    7: "由北向南",
    8: "其他",
}
CAR_DIRECTIONS = {0: "從上往下", 1: "從下往上"}
VEHICLE_TYPES = {
    0: "未知",
    1: "客車",
    2: "貨車",
    3: "轎車",
    4: "麵包車",
    5: "小貨車",
    6: "行人",
    7: "二輪車",
    8: "三輪車",
    9: "SUV/MPV",
    10: "中型客車",
    11: "機動車",
    12: "非機動車",
    13: "小型轎車",
    14: "微型轎車",
    15: "皮卡",
}
VEHICLE_COLORS = {
    0: "其他",
    1: "白色",
    2: "銀色",
    3: "灰色",
    4: "黑色",
    5: "紅色",
    6: "深藍",
    7: "藍色",
    8: "黃色",
    9: "綠色",
    10: "棕色",
    11: "粉色",
    12: "紫色",
    13: "深灰",
    14: "青色",
}
DETECT_TYPES = {
    0: "車輛檢測",
    1: "地感觸發",
    2: "視頻觸發",
    3: "多幀識別",
    4: "雷達觸發",
    5: "混行檢測",
}
COLOR_PREFIX = "蓝黄绿白黑"
PLATE_RE = re.compile(r"^[\u4e00-\u9fff][A-Z0-9挂学警港澳]{4,8}$")
MAX_IMAGE = 8_000_000


def decode_gbk(raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip("\x00 ").strip()
    data = bytes(raw).split(b"\x00", 1)[0].strip()
    if not data:
        return ""
    return data.decode("gbk", errors="replace").strip()


def clean_plate(text: str) -> str:
    text = (text or "").strip()
    if len(text) > 1 and text[0] in COLOR_PREFIX:
        return text[1:].strip()
    return text


def _u8(value) -> int:
    return int(value) & 0xFF


def plate_score(plate: str, confidence: int) -> int:
    score = 0
    if not plate:
        return 0
    if "\ufffd" in plate or any(ord(ch) < 32 for ch in plate):
        return 0
    if PLATE_RE.match(plate):
        score += 50
    elif re.search(r"[A-Z0-9]{4,}", plate) and re.search(r"[\u4e00-\u9fff]", plate):
        score += 30
    elif re.search(r"[A-Z0-9]{5,}", plate):
        score += 15
    else:
        score += 1
    if 1 <= confidence <= 100:
        score += 10
    return score


def _as_hk(stamp: str) -> str:
    if not stamp or "+" in stamp:
        return stamp
    return stamp + "+08:00"


def _abs_time(raw) -> str:
    text = decode_gbk(raw)
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 14:
        return ""
    y, m, d = digits[0:4], digits[4:6], digits[6:8]
    hh, mm, ss = digits[8:10], digits[10:12], digits[12:14]
    ms = digits[14:17]
    stamp = f"{y}-{m}-{d}T{hh}:{mm}:{ss}"
    if ms:
        stamp += f".{ms}"
    return _as_hk(stamp)


def _time_v30(value) -> str:
    year = int(value.wYear)
    if year < 2000 or year > 2100:
        return ""
    ms = int(value.wMilliSec)
    stamp = (
        f"{year:04d}-{_u8(value.byMonth):02d}-{_u8(value.byDay):02d}"
        f"T{_u8(value.byHour):02d}:{_u8(value.byMinute):02d}:{_u8(value.bySecond):02d}"
    )
    if ms:
        stamp += f".{ms:03d}"
    return _as_hk(stamp)


def _plausible_ptr(ptr, length) -> bool:
    if not ptr or length <= 0 or length > MAX_IMAGE:
        return False
    if ptr < 0x10000 or ptr > 0x00007FFFFFFFFFFF:
        return False
    return True


def _copy_bytes(ptr, length) -> bytes:
    if not _plausible_ptr(ptr, length):
        return b""
    try:
        return ctypes.string_at(ptr, length)
    except (ValueError, OSError):
        return b""


def _image_from_pic(pic) -> dict | None:
    length = int(pic.dwDataLen)
    kind = _u8(pic.byType)
    data_type = _u8(pic.byDataType)
    blob = _copy_bytes(pic.pBuffer, length)
    if not blob:
        return None
    when = _abs_time(pic.byAbsTime)
    if data_type == 1:
        url = blob.split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()
        if url.startswith("http"):
            return {"kind": kind, "url": url, "time": when}
        return None
    if blob[:2] != b"\xff\xd8":
        return None
    return {"kind": kind, "bytes": blob, "time": when}


def _fill_common(event, plate_info, vehicle, direction, detect, car_direction, lane, vehicle_type):
    raw = decode_gbk(plate_info.sLicense)
    plate = clean_plate(raw)
    confidence = _u8(plate_info.byEntireBelieve)
    if car_direction is None:
        car_text = ""
    else:
        car_text = CAR_DIRECTIONS.get(_u8(car_direction), "")
    event.update(
        {
            "plateRaw": raw,
            "plate": plate,
            "plateColor": PLATE_COLORS.get(_u8(plate_info.byColor), str(_u8(plate_info.byColor))),
            "plateType": PLATE_TYPES.get(_u8(plate_info.byPlateType), str(_u8(plate_info.byPlateType))),
            "confidence": confidence,
            "lane": _u8(lane),
            "direction": DIRECTIONS.get(_u8(direction), str(_u8(direction)) if _u8(direction) else ""),
            "carDirection": car_text,
            "vehicleType": VEHICLE_TYPES.get(_u8(vehicle_type), str(_u8(vehicle_type)) if _u8(vehicle_type) else ""),
            "vehicleColor": VEHICLE_COLORS.get(_u8(vehicle.byColor), ""),
            "speed": int(vehicle.wSpeed),
            "detectType": DETECT_TYPES.get(_u8(detect), ""),
            "monitoringSite": decode_gbk(event.get("_site", b"")),
            "deviceNo": decode_gbk(event.get("_device_no", b"")),
        }
    )
    event["score"] = plate_score(plate, confidence)
    event.pop("_site", None)
    event.pop("_device_no", None)
    return event


def _read_its(cls, address, layout_name, pack, copy_images: bool):
    obj = cls.from_address(address)
    event = {
        "layout": layout_name,
        "pack": pack,
        "structSize": ctypes.sizeof(cls),
        "command": COMM_ITS_PLATE_RESULT,
        "_site": bytes(obj.byMonitoringSiteID),
        "_device_no": bytes(obj.byDeviceID),
        "images": [],
        "time": "",
    }
    lane = obj.byDriveChan
    vehicle_type = getattr(obj, "byVehicleType", 0)
    direction = obj.byDir
    detect = obj.byDetectType
    car_direction = obj.byCarDirectionType if hasattr(obj, "byCarDirectionType") else None
    if hasattr(obj, "struSnapFirstPicTime"):
        event["time"] = _time_v30(obj.struSnapFirstPicTime)
    _fill_common(event, obj.struPlateInfo, obj.struVehicleInfo, direction, detect, car_direction, lane, vehicle_type)
    if int(obj.struVehicleInfo.byVehicleType) and not _u8(vehicle_type):
        event["vehicleType"] = VEHICLE_TYPES.get(
            _u8(obj.struVehicleInfo.byVehicleType), event["vehicleType"]
        )
    if copy_images:
        count = max(0, min(int(obj.dwPicNum), 6))
        if count == 0:
            count = 6
        for index in range(count):
            image = _image_from_pic(obj.struPicInfo[index])
            if image:
                event["images"].append(image)
                if not event["time"] and image.get("time"):
                    event["time"] = image["time"]
        if count == 6 and not event["images"]:
            event["images"] = []
    return event


def _read_upload(cls, address, pack, copy_images: bool):
    obj = cls.from_address(address)
    event = {
        "layout": "UploadPlate",
        "pack": pack,
        "structSize": ctypes.sizeof(cls),
        "command": COMM_UPLOAD_PLATE_RESULT,
        "images": [],
        "time": _abs_time(obj.byAbsTime),
        "monitoringSite": "",
        "deviceNo": "",
    }
    _fill_common(
        event,
        obj.struPlateInfo,
        obj.struVehicleInfo,
        0,
        0,
        None,
        obj.byDriveChan,
        obj.byVehicleType,
    )
    if copy_images:
        scene = _copy_bytes(obj.pBuffer1, int(obj.dwPicLen))
        plate = _copy_bytes(obj.pBuffer2, int(obj.dwPicPlateLen))
        if scene[:2] == b"\xff\xd8":
            event["images"].append({"kind": 1, "bytes": scene, "time": event["time"]})
        if plate[:2] == b"\xff\xd8":
            event["images"].append({"kind": 0, "bytes": plate, "time": event["time"]})
    return event


def _read_layout(layout: dict, address: int, copy_images: bool) -> dict:
    if layout["kind"] == "upload":
        return _read_upload(layout["cls"], address, layout["pack"], copy_images)
    return _read_its(layout["cls"], address, layout["name"], layout["pack"], copy_images)


def read_user_id(alarmer) -> int:
    if not alarmer:
        return -1
    try:
        return c_int32.from_address(alarmer + 8).value
    except (ValueError, OSError):
        return -1


def read_device_ip(alarmer) -> str:
    if not alarmer:
        return ""
    for pack in (4, 1, None):
        cls = get_structs(pack)["Alarmer"]
        try:
            text = decode_gbk(cls.from_address(alarmer).sDeviceIP)
        except (ValueError, OSError):
            continue
        if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", text):
            return text
    return ""


def parse_alarm(command: int, address: int, buf_len: int) -> dict | None:
    if not address:
        return None
    try:
        dw_size = c_uint32.from_address(address).value
    except (ValueError, OSError):
        return None
    limit = buf_len or dw_size
    if limit <= 0:
        return None

    matched = []
    preview = []
    for layout in all_layouts():
        if command == COMM_UPLOAD_PLATE_RESULT and layout["kind"] != "upload":
            continue
        if command == COMM_ITS_PLATE_RESULT and layout["kind"] != "its":
            continue
        if command not in (COMM_ITS_PLATE_RESULT, COMM_UPLOAD_PLATE_RESULT):
            continue
        size = layout["size"]
        if size > limit:
            continue
        try:
            event = _read_layout(layout, address, False)
        except (ValueError, OSError):
            continue
        event["reportedSize"] = dw_size
        preview.append(event)
        if size == dw_size or (buf_len and size == buf_len):
            matched.append((layout, event))

    if not preview:
        return {
            "command": command,
            "reportedSize": dw_size,
            "plate": "",
            "score": 0,
            "images": [],
            "parseWarning": f"報警 0x{command:04X} 長度 {dw_size} 對不上已知卡口結構",
        }

    pool = [item[1] for item in matched] or preview
    best = max(pool, key=lambda item: item["score"])
    if matched:
        matched.sort(key=lambda item: item[1]["score"], reverse=True)
        layout = matched[0][0]
        try:
            copied = _read_layout(layout, address, True)
            copied["reportedSize"] = dw_size
            copied["score"] = plate_score(copied.get("plate", ""), copied.get("confidence") or 0)
            if copied["score"] >= best["score"]:
                best = copied
        except (ValueError, OSError):
            pass

    if best["score"] < 15:
        best["parseWarning"] = (
            f"結構大小 {dw_size}，採用 {best.get('layout')} / pack {best.get('pack')}，車牌信心不足"
        )
    best.pop("score", None)
    return best
