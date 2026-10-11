"""白名單與管理頁通行碼。未列入名單的 IP 看不到主頁。"""

from __future__ import annotations

import ipaddress
import secrets

from fastapi import Request

_sessions: set[str] = set()


def client_ip(request: Request) -> str:
    host = request.client.host if request.client else ""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded and _trusted_proxy(host):
        return forwarded.split(",")[0].strip()
    return host


def _trusted_proxy(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback


def ip_allowed(ip: str, whitelist: list[str]) -> bool:
    if not whitelist:
        return True
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    for item in whitelist:
        try:
            if "/" in item:
                if addr in ipaddress.ip_network(item, strict=False):
                    return True
            elif addr == ipaddress.ip_address(item):
                return True
        except ValueError:
            continue
    return False


def new_session() -> str:
    token = secrets.token_urlsafe(32)
    _sessions.add(token)
    return token


def valid_session(token: str | None) -> bool:
    return bool(token) and token in _sessions


def drop_session(token: str | None) -> None:
    if token:
        _sessions.discard(token)
