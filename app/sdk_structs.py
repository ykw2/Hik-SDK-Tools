"""海康卡口車牌結構。

Linux SDK 多半 pack(4)，Windows SDK pack(1)。車牌本體在結構中段，
前後欄位隨 SDK 版本增減，所以準備多種佈局，執行時用 dwSize 對上。
指標只在佈局大小吻合時才讀，避免讀到錯誤位址。
"""

from __future__ import annotations

import ctypes
from ctypes import Structure, c_float, c_int32, c_uint8, c_uint16, c_uint32, c_void_p

COMM_ITS_PLATE_RESULT = 0x3050
COMM_UPLOAD_PLATE_RESULT = 0x2800

_CACHE: dict = {}



def get_structs(pack):
    """pack 為 1、4，或 None（編譯器自然對齊）。"""
    if pack not in _CACHE:
        _CACHE[pack] = _build_packed(pack)
    return _CACHE[pack]


def all_layouts():
    layouts = []
    for pack in (4, 1, None):
        structs = get_structs(pack)
        for name in ("ItsV0", "ItsV1", "ItsV3", "ItsV2", "UploadPlate"):
            cls = structs[name]
            layouts.append(
                {
                    "name": name,
                    "pack": pack if pack is not None else 0,
                    "cls": cls,
                    "size": ctypes.sizeof(cls),
                    "kind": "upload" if name == "UploadPlate" else "its",
                }
            )
    return layouts



def _build_packed(pack):
    """在 class 本體設定 _pack_，巢狀欄位才會對齊。"""
    header = ""
    if pack in (1, 4):
        header = f"_pack_ = {pack}\n    "

    code = f"""
class VcaRect(Structure):
    {header}_fields_ = [
        ("fX", c_float), ("fY", c_float), ("fWidth", c_float), ("fHeight", c_float),
    ]

class TimeV30(Structure):
    {header}_fields_ = [
        ("wYear", c_uint16),
        ("byMonth", c_uint8), ("byDay", c_uint8), ("byHour", c_uint8),
        ("byMinute", c_uint8), ("bySecond", c_uint8), ("byISO8601", c_uint8),
        ("wMilliSec", c_uint16),
        ("cTimeDifferenceH", c_uint8), ("cTimeDifferenceM", c_uint8),
    ]

class PlateV1(Structure):
    {header}_fields_ = [
        ("byPlateType", c_uint8), ("byColor", c_uint8), ("byBright", c_uint8),
        ("byLicenseLen", c_uint8), ("byEntireBelieve", c_uint8),
        ("byRegion", c_uint8), ("byCountry", c_uint8), ("byRes", c_uint8 * 33),
        ("struPlateRect", VcaRect),
        ("sLicense", c_uint8 * 16), ("byBelieve", c_uint8 * 16),
    ]

class PlateV2(Structure):
    {header}_fields_ = [
        ("byPlateType", c_uint8), ("byColor", c_uint8), ("byBright", c_uint8),
        ("byLicenseLen", c_uint8), ("byEntireBelieve", c_uint8),
        ("byRegion", c_uint8), ("byCountry", c_uint8), ("byArea", c_uint8),
        ("byPlateSize", c_uint8), ("byAddInfoFlag", c_uint8),
        ("wCRIndex", c_uint16), ("byRes", c_uint8 * 4),
        ("pAddInfoBuffer", c_void_p), ("sPlateCategory", c_uint8 * 8),
        ("dwXmlLen", c_uint32), ("pXmlBuf", c_void_p),
        ("struPlateRect", VcaRect),
        ("sLicense", c_uint8 * 16), ("byBelieve", c_uint8 * 16),
    ]

class VehicleInfo(Structure):
    {header}_fields_ = [
        ("dwIndex", c_uint32),
        ("byVehicleType", c_uint8), ("byColorDepth", c_uint8), ("byColor", c_uint8), ("byRes1", c_uint8),
        ("wSpeed", c_uint16), ("wLength", c_uint16),
        ("byIllegalType", c_uint8), ("byVehicleLogoRecog", c_uint8),
        ("byVehicleSubLogoRecog", c_uint8), ("byVehicleModel", c_uint8),
        ("byCustomInfo", c_uint8 * 16), ("wVehicleLogoRecog", c_uint16), ("byRes3", c_uint8 * 14),
    ]

class PictureInfo(Structure):
    {header}_fields_ = [
        ("dwDataLen", c_uint32),
        ("byType", c_uint8), ("byDataType", c_uint8), ("byCloseUpType", c_uint8), ("byPicRecogMode", c_uint8),
        ("dwRedLightTime", c_uint32), ("byAbsTime", c_uint8 * 32),
        ("struPlateRect", VcaRect), ("struPlateRecgRect", VcaRect),
        ("pBuffer", c_void_p), ("dwUTCTime", c_uint32),
        ("byCompatibleAblity", c_uint8), ("byTimeDiffFlag", c_uint8),
        ("cTimeDifferenceH", c_uint8), ("cTimeDifferenceM", c_uint8), ("byRes2", c_uint8 * 4),
    ]

class ItsV0(Structure):
    {header}_fields_ = [
        ("dwSize", c_uint32), ("dwMatchNo", c_uint32),
        ("byGroupNum", c_uint8), ("byPicNo", c_uint8), ("bySecondCam", c_uint8), ("byFeaturePicNo", c_uint8),
        ("byDriveChan", c_uint8), ("byRes1", c_uint8 * 3),
        ("wIllegalType", c_uint16), ("byIllegalSubType", c_uint8 * 8),
        ("byPostPicNo", c_uint8), ("byChanIndex", c_uint8), ("wSpeedLimit", c_uint16), ("byRes2", c_uint8 * 2),
        ("struPlateInfo", PlateV1), ("struVehicleInfo", VehicleInfo),
        ("byMonitoringSiteID", c_uint8 * 48), ("byDeviceID", c_uint8 * 48),
        ("byDir", c_uint8), ("byDetectType", c_uint8), ("byRes3", c_uint8 * 38),
        ("dwPicNum", c_uint32), ("struPicInfo", PictureInfo * 6),
    ]

class ItsV1(Structure):
    {header}_fields_ = [
        ("dwSize", c_uint32), ("dwMatchNo", c_uint32),
        ("byGroupNum", c_uint8), ("byPicNo", c_uint8), ("bySecondCam", c_uint8), ("byFeaturePicNo", c_uint8),
        ("byDriveChan", c_uint8), ("byVehicleType", c_uint8), ("byDetSceneID", c_uint8), ("byVehicleAttribute", c_uint8),
        ("wIllegalType", c_uint16), ("byIllegalSubType", c_uint8 * 8),
        ("byPostPicNo", c_uint8), ("byChanIndex", c_uint8), ("wSpeedLimit", c_uint16), ("byRes2", c_uint8 * 2),
        ("struPlateInfo", PlateV1), ("struVehicleInfo", VehicleInfo),
        ("byMonitoringSiteID", c_uint8 * 48), ("byDeviceID", c_uint8 * 48),
        ("byDir", c_uint8), ("byDetectType", c_uint8), ("byRelaLaneDirectionType", c_uint8), ("byCarDirectionType", c_uint8),
        ("dwCustomIllegalType", c_uint32), ("byIllegalFromatType", c_uint8), ("pIllegalInfoBuf", c_void_p), ("byRes4", c_uint8 * 4),
        ("byDataAnalysis", c_uint8), ("byYellowLabelCar", c_uint8), ("byDangerousVehicles", c_uint8),
        ("byPilotSafebelt", c_uint8), ("byCopilotSafebelt", c_uint8),
        ("byPilotSunVisor", c_uint8), ("byCopilotSunVisor", c_uint8), ("byPilotCall", c_uint8),
        ("byBarrierGateCtrlType", c_uint8), ("byAlarmDataType", c_uint8),
        ("struSnapFirstPicTime", TimeV30), ("dwIllegalTime", c_uint32), ("dwPicNum", c_uint32),
        ("struPicInfo", PictureInfo * 6),
    ]

class ItsV3(Structure):
    {header}_fields_ = [
        ("dwSize", c_uint32), ("dwMatchNo", c_uint32),
        ("byGroupNum", c_uint8), ("byPicNo", c_uint8), ("bySecondCam", c_uint8), ("byFeaturePicNo", c_uint8),
        ("byDriveChan", c_uint8), ("byVehicleType", c_uint8), ("byDetSceneID", c_uint8), ("byVehicleAttribute", c_uint8),
        ("wIllegalType", c_uint16), ("byIllegalSubType", c_uint8 * 8),
        ("byPostPicNo", c_uint8), ("byChanIndex", c_uint8), ("wSpeedLimit", c_uint16),
        ("byChanIndexEx", c_uint8), ("byRes2", c_uint8),
        ("struPlateInfo", PlateV1), ("struVehicleInfo", VehicleInfo),
        ("byMonitoringSiteID", c_uint8 * 48), ("byDeviceID", c_uint8 * 48),
        ("byDir", c_uint8), ("byDetectType", c_uint8), ("byRelaLaneDirectionType", c_uint8), ("byCarDirectionType", c_uint8),
        ("dwCustomIllegalType", c_uint32), ("pIllegalInfoBuf", c_void_p),
        ("byIllegalFromatType", c_uint8), ("byPendant", c_uint8), ("byDataAnalysis", c_uint8),
        ("byYellowLabelCar", c_uint8), ("byDangerousVehicles", c_uint8),
        ("byPilotSafebelt", c_uint8), ("byCopilotSafebelt", c_uint8),
        ("byPilotSunVisor", c_uint8), ("byCopilotSunVisor", c_uint8), ("byPilotCall", c_uint8),
        ("byBarrierGateCtrlType", c_uint8), ("byAlarmDataType", c_uint8),
        ("struSnapFirstPicTime", TimeV30), ("dwIllegalTime", c_uint32), ("dwPicNum", c_uint32),
        ("struPicInfo", PictureInfo * 6),
    ]

class ItsV2(Structure):
    {header}_fields_ = [
        ("dwSize", c_uint32), ("dwMatchNo", c_uint32),
        ("byGroupNum", c_uint8), ("byPicNo", c_uint8), ("bySecondCam", c_uint8), ("byFeaturePicNo", c_uint8),
        ("byDriveChan", c_uint8), ("byVehicleType", c_uint8), ("byDetSceneID", c_uint8), ("byVehicleAttribute", c_uint8),
        ("wIllegalType", c_uint16), ("byIllegalSubType", c_uint8 * 8),
        ("byPostPicNo", c_uint8), ("byChanIndex", c_uint8), ("wSpeedLimit", c_uint16),
        ("byChanIndexEx", c_uint8), ("byRes2", c_uint8),
        ("struPlateInfo", PlateV2), ("struVehicleInfo", VehicleInfo),
        ("byMonitoringSiteID", c_uint8 * 48), ("byDeviceID", c_uint8 * 48),
        ("byDir", c_uint8), ("byDetectType", c_uint8), ("byRelaLaneDirectionType", c_uint8), ("byCarDirectionType", c_uint8),
        ("dwCustomIllegalType", c_uint32), ("pIllegalInfoBuf", c_void_p),
        ("byIllegalFromatType", c_uint8), ("byPendant", c_uint8), ("byDataAnalysis", c_uint8),
        ("byYellowLabelCar", c_uint8), ("byDangerousVehicles", c_uint8),
        ("byPilotSafebelt", c_uint8), ("byCopilotSafebelt", c_uint8),
        ("byPilotSunVisor", c_uint8), ("byCopilotSunVisor", c_uint8), ("byPilotCall", c_uint8),
        ("byBarrierGateCtrlType", c_uint8), ("byAlarmDataType", c_uint8),
        ("struSnapFirstPicTime", TimeV30), ("dwIllegalTime", c_uint32), ("dwPicNum", c_uint32),
        ("struPicInfo", PictureInfo * 6),
    ]

class UploadPlate(Structure):
    {header}_fields_ = [
        ("dwSize", c_uint32), ("byResultType", c_uint8), ("byChanIndex", c_uint8), ("wAlarmRecordID", c_uint16),
        ("dwRelativeTime", c_uint32), ("byAbsTime", c_uint8 * 32),
        ("dwPicLen", c_uint32), ("dwPicPlateLen", c_uint32), ("dwVideoLen", c_uint32),
        ("byTrafficLight", c_uint8), ("byPicNum", c_uint8), ("byDriveChan", c_uint8), ("byVehicleType", c_uint8),
        ("dwBinPicLen", c_uint32), ("dwCarPicLen", c_uint32), ("dwFarCarPicLen", c_uint32),
        ("pBuffer3", c_void_p), ("pBuffer4", c_void_p), ("pBuffer5", c_void_p),
        ("byRelaLaneDirectionType", c_uint8), ("byRes3", c_uint8 * 7),
        ("struPlateInfo", PlateV1), ("struVehicleInfo", VehicleInfo),
        ("pBuffer1", c_void_p), ("pBuffer2", c_void_p),
    ]

class Alarmer(Structure):
    {header}_fields_ = [
        ("byUserIDValid", c_uint8), ("bySerialValid", c_uint8), ("byVersionValid", c_uint8),
        ("byDeviceNameValid", c_uint8), ("byMacAddrValid", c_uint8), ("byLinkPortValid", c_uint8),
        ("byDeviceIPValid", c_uint8), ("bySocketIPValid", c_uint8),
        ("lUserID", c_int32), ("sSerialNumber", c_uint8 * 48), ("dwDeviceVersion", c_uint32),
        ("sDeviceName", c_uint8 * 32), ("byMacAddr", c_uint8 * 6), ("wLinkPort", c_uint16),
        ("sDeviceIP", c_uint8 * 128), ("sSocketIP", c_uint8 * 128),
        ("byIpProtocol", c_uint8), ("byRes2", c_uint8 * 11),
    ]

class LocalSdkPath(Structure):
    {header}_fields_ = [("sPath", c_char * 256), ("byRes", c_uint8 * 128)]

class LoginInfo(Structure):
    {header}_fields_ = [
        ("sDeviceAddress", c_char * 129), ("byUseTransport", c_uint8), ("wPort", c_uint16),
        ("sUserName", c_char * 64), ("sPassword", c_char * 64),
        ("cbLoginResult", c_void_p), ("pUser", c_void_p), ("bUseAsynLogin", c_int32),
        ("byProxyType", c_uint8), ("byUseUTCTime", c_uint8), ("byLoginMode", c_uint8), ("byHttps", c_uint8),
        ("iProxyID", c_int32), ("byVerifyMode", c_uint8), ("byRes3", c_uint8 * 119),
        ("byExtra", c_uint8 * 256),
    ]

class SetupAlarm(Structure):
    {header}_fields_ = [
        ("dwSize", c_uint32), ("byLevel", c_uint8), ("byAlarmInfoType", c_uint8),
        ("byRetAlarmTypeV40", c_uint8), ("byRetDevInfoVersion", c_uint8),
        ("byRetVQDAlarmType", c_uint8), ("byFaceAlarmDetection", c_uint8),
        ("bySupport", c_uint8), ("byBrokenNetHttp", c_uint8), ("wTaskNo", c_uint16),
        ("byDeployType", c_uint8), ("byRes1", c_uint8 * 3),
        ("byAlarmTypeURL", c_uint8), ("byCustomCtrl", c_uint8), ("byRes4", c_uint8 * 128),
    ]
"""
    glob = {
        "Structure": Structure,
        "c_float": c_float,
        "c_uint8": c_uint8,
        "c_uint16": c_uint16,
        "c_uint32": c_uint32,
        "c_int32": c_int32,
        "c_void_p": c_void_p,
        "c_char": ctypes.c_char,
    }
    exec(code, glob)
    names = [
        "VcaRect",
        "TimeV30",
        "PlateV1",
        "PlateV2",
        "VehicleInfo",
        "PictureInfo",
        "ItsV0",
        "ItsV1",
        "ItsV3",
        "ItsV2",
        "UploadPlate",
        "Alarmer",
        "LocalSdkPath",
        "LoginInfo",
        "SetupAlarm",
    ]
    return {name: glob[name] for name in names}
