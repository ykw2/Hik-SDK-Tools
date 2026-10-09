"""載入 HCNetSDK，登入卡口並布防。回調只做拆包，不在裡面寫檔或轉發。"""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import c_int32, c_uint32, c_void_p
from pathlib import Path

from app.parser import parse_alarm, read_device_ip, read_user_id
from app.sdk_structs import COMM_ITS_PLATE_RESULT, COMM_UPLOAD_PLATE_RESULT, get_structs

log = logging.getLogger(__name__)

ERRORS = {
    1: "帳號或密碼錯誤",
    3: "SDK 未初始化",
    4: "通道號錯誤",
    5: "超過最大連線數",
    7: "連線鏡頭失敗",
    8: "SDK 版本不符",
    10: "接收資料逾時",
    11: "傳送資料逾時",
    17: "參數錯誤",
    23: "裝置不支援",
    28: "資源不足",
    41: "SDK 資源配置錯誤",
    46: "用戶不存在",
    47: "用戶被鎖定",
    153: "帳號已鎖定",
}

NET_SDK_INIT_CFG_SDK_PATH = 2
NET_SDK_INIT_CFG_LIBEAY_PATH = 3
NET_SDK_INIT_CFG_SSLEAY_PATH = 4

if os.name == "nt":
    CALLBACK = ctypes.WINFUNCTYPE
else:
    CALLBACK = ctypes.CFUNCTYPE

MSG_CALLBACK = CALLBACK(None, c_int32, c_void_p, c_void_p, c_uint32, c_void_p)
EX_CALLBACK = CALLBACK(None, c_uint32, c_int32, c_int32, c_void_p)


class SdkClient:
    def __init__(self, on_alarm, on_exception):
        self.on_alarm = on_alarm
        self.on_exception = on_exception
        self.lib = None
        self.loaded = False
        self.error = ""
        self._msg_cb = None
        self._ex_cb = None
        self._kept = []

    def init(self, sdk_path: Path, log_dir: Path | None = None):
        sdk_path = Path(sdk_path)
        library = self._library_file(sdk_path)
        if library is None:
            self.error = f"在 {sdk_path} 找不到 libhcnetsdk.so 或 HCNetSDK.dll"
            return
        try:
            self.lib = self._load(sdk_path, library)
            self._bind()
            self._prepare_paths(sdk_path)
            if not self.lib.NET_DVR_Init():
                self.error = f"NET_DVR_Init 失敗，{self.last_error()}"
                return
            self.lib.NET_DVR_SetConnectTime(5000, 3)
            self.lib.NET_DVR_SetReconnect(10000, 1)
            if log_dir and os.environ.get("HIKSDK_LOG") == "1":
                log_dir.mkdir(parents=True, exist_ok=True)
                self.lib.NET_DVR_SetLogToFile(1, str(log_dir).encode("utf-8"), 0)
            self._install_callbacks()
        except OSError as exc:
            self.error = f"載入 SDK 失敗：{exc}"
            self.lib = None
            return
        except Exception as exc:
            log.exception("SDK 初始化失敗")
            self.error = f"SDK 初始化失敗：{exc}"
            return
        self.loaded = True
        self.error = ""

    def cleanup(self):
        if self.lib and self.loaded:
            try:
                self.lib.NET_DVR_Cleanup()
            except Exception:
                log.exception("SDK cleanup")
        self.loaded = False

    def last_error(self) -> str:
        if not self.lib:
            return self.error or "SDK 未載入"
        try:
            code = int(self.lib.NET_DVR_GetLastError())
        except Exception:
            return "無法取得錯誤碼"
        text = ERRORS.get(code, "錯誤")
        return f"{text}（{code}）"

    def login(self, host: str, port: int, username: str, password: str) -> int:
        structs = get_structs(1 if os.name == "nt" else 4)
        info = structs["LoginInfo"]()
        info.sDeviceAddress = host.encode("utf-8")
        info.wPort = int(port)
        info.sUserName = username.encode("utf-8")
        info.sPassword = password.encode("utf-8")
        info.bUseAsynLogin = 0
        info.byLoginMode = 0
        device = ctypes.create_string_buffer(2048)
        user_id = self.lib.NET_DVR_Login_V40(ctypes.addressof(info), ctypes.addressof(device))
        return int(user_id)

    def logout(self, user_id: int):
        if self.lib and user_id >= 0:
            self.lib.NET_DVR_Logout(int(user_id))

    def arm(self, user_id: int) -> int:
        structs = get_structs(1 if os.name == "nt" else 4)
        param = structs["SetupAlarm"]()
        param.dwSize = 20
        param.byLevel = 1
        param.byAlarmInfoType = 1
        param.byDeployType = 1
        handle = self.lib.NET_DVR_SetupAlarmChan_V41(int(user_id), ctypes.addressof(param))
        return int(handle)

    def disarm(self, handle: int):
        if self.lib and handle >= 0:
            self.lib.NET_DVR_CloseAlarmChan_V30(int(handle))

    def _library_file(self, sdk_path: Path) -> Path | None:
        names = ("HCNetSDK.dll",) if os.name == "nt" else ("libhcnetsdk.so",)
        for name in names:
            candidate = sdk_path / name
            if candidate.is_file():
                return candidate
        return None

    def _load(self, sdk_path: Path, library: Path):
        if os.name == "nt":
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(sdk_path))
                com = sdk_path / "HCNetSDKCom"
                if com.is_dir():
                    os.add_dll_directory(str(com))
            return ctypes.WinDLL(str(library))
        mode = ctypes.RTLD_GLOBAL
        preferred = [
            "libcrypto.so.1.1",
            "libssl.so.1.1",
            "libcrypto.so",
            "libssl.so",
            "libHCCore.so",
            "libhpr.so",
        ]
        loaded = set()
        for name in preferred:
            self._cdll(sdk_path / name, mode, loaded)
        for folder in (sdk_path, sdk_path / "HCNetSDKCom"):
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.so*")):
                if path.name.startswith("libhcnetsdk"):
                    continue
                self._cdll(path, mode, loaded)
        return ctypes.CDLL(str(library), mode=mode)

    def _cdll(self, path: Path, mode: int, loaded: set):
        if not path.is_file() or path.name in loaded:
            return
        try:
            ctypes.CDLL(str(path), mode=mode)
            loaded.add(path.name)
        except OSError as exc:
            log.warning("略過 %s：%s", path.name, exc)

    def _bind(self):
        lib = self.lib
        lib.NET_DVR_Init.restype = c_int32
        lib.NET_DVR_Cleanup.restype = c_int32
        lib.NET_DVR_GetLastError.restype = c_uint32
        lib.NET_DVR_SetConnectTime.argtypes = [c_uint32, c_uint32]
        lib.NET_DVR_SetReconnect.argtypes = [c_uint32, c_int32]
        lib.NET_DVR_SetSDKInitCfg.argtypes = [c_int32, c_void_p]
        lib.NET_DVR_SetSDKInitCfg.restype = c_int32
        lib.NET_DVR_Login_V40.argtypes = [c_void_p, c_void_p]
        lib.NET_DVR_Login_V40.restype = c_int32
        lib.NET_DVR_Logout.argtypes = [c_int32]
        lib.NET_DVR_Logout.restype = c_int32
        lib.NET_DVR_SetupAlarmChan_V41.argtypes = [c_int32, c_void_p]
        lib.NET_DVR_SetupAlarmChan_V41.restype = c_int32
        lib.NET_DVR_CloseAlarmChan_V30.argtypes = [c_int32]
        lib.NET_DVR_CloseAlarmChan_V30.restype = c_int32
        if hasattr(lib, "NET_DVR_SetLogToFile"):
            lib.NET_DVR_SetLogToFile.argtypes = [c_uint32, ctypes.c_char_p, c_int32]

    def _prepare_paths(self, sdk_path: Path):
        structs = get_structs(1 if os.name == "nt" else 4)
        local = structs["LocalSdkPath"]()
        folder = str(sdk_path)
        if not folder.endswith(("\\", "/")):
            folder += "\\" if os.name == "nt" else "/"
        local.sPath = folder.encode("utf-8")
        self._kept.append(local)
        self.lib.NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_SDK_PATH, ctypes.addressof(local))
        crypto = self._first_existing(
            sdk_path,
            ("libcrypto.so.1.1", "libcrypto.so", "libeay32.dll"),
        )
        ssl = self._first_existing(sdk_path, ("libssl.so.1.1", "libssl.so", "ssleay32.dll"))
        if crypto:
            buf = ctypes.create_string_buffer(str(crypto).encode("utf-8"))
            self._kept.append(buf)
            self.lib.NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_LIBEAY_PATH, ctypes.addressof(buf))
        if ssl:
            buf = ctypes.create_string_buffer(str(ssl).encode("utf-8"))
            self._kept.append(buf)
            self.lib.NET_DVR_SetSDKInitCfg(NET_SDK_INIT_CFG_SSLEAY_PATH, ctypes.addressof(buf))

    def _first_existing(self, folder: Path, names: tuple[str, ...]) -> Path | None:
        for name in names:
            path = folder / name
            if path.is_file():
                return path
        return None

    def _install_callbacks(self):
        self._msg_cb = MSG_CALLBACK(self._on_message)
        self._ex_cb = EX_CALLBACK(self._on_exception_cb)
        lib = self.lib
        if hasattr(lib, "NET_DVR_SetDVRMessageCallBack_V50"):
            lib.NET_DVR_SetDVRMessageCallBack_V50.argtypes = [c_int32, MSG_CALLBACK, c_void_p]
            lib.NET_DVR_SetDVRMessageCallBack_V50.restype = c_int32
            if not lib.NET_DVR_SetDVRMessageCallBack_V50(0, self._msg_cb, None):
                raise OSError(f"登記報警回調失敗，{self.last_error()}")
        elif hasattr(lib, "NET_DVR_SetDVRMessageCallBack_V30"):
            lib.NET_DVR_SetDVRMessageCallBack_V30.argtypes = [MSG_CALLBACK, c_void_p]
            lib.NET_DVR_SetDVRMessageCallBack_V30.restype = c_int32
            if not lib.NET_DVR_SetDVRMessageCallBack_V30(self._msg_cb, None):
                raise OSError(f"登記報警回調失敗，{self.last_error()}")
        else:
            raise OSError("SDK 沒有報警回調函數")
        if hasattr(lib, "NET_DVR_SetExceptionCallBack_V30"):
            lib.NET_DVR_SetExceptionCallBack_V30.argtypes = [c_uint32, c_void_p, EX_CALLBACK, c_void_p]
            lib.NET_DVR_SetExceptionCallBack_V30.restype = c_int32
            lib.NET_DVR_SetExceptionCallBack_V30(0, None, self._ex_cb, None)

    def _on_message(self, command, alarmer, info, buf_len, _user):
        try:
            command = int(command)
            if command not in (COMM_ITS_PLATE_RESULT, COMM_UPLOAD_PLATE_RESULT):
                self.on_alarm({"commandOnly": command})
                return
            parsed = parse_alarm(int(command), int(info or 0), int(buf_len))
            if not parsed:
                return
            parsed["userId"] = read_user_id(int(alarmer or 0))
            parsed["deviceIp"] = read_device_ip(int(alarmer or 0))
            self.on_alarm(parsed)
        except Exception:
            log.exception("處理車牌回調失敗")

    def _on_exception_cb(self, typ, user_id, _handle, _user):
        try:
            self.on_exception(int(typ), int(user_id))
        except Exception:
            log.exception("處理 SDK 異常回調失敗")
