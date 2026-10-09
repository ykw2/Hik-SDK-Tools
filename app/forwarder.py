"""把車牌事件套進 JSON 模板，再 POST 到設定的位址。"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


def render_template(template: Any, event: dict) -> Any:
    if isinstance(template, dict):
        return {key: render_template(value, event) for key, value in template.items()}
    if isinstance(template, list):
        return [render_template(value, event) for value in template]
    if isinstance(template, str):
        whole = PLACEHOLDER.fullmatch(template.strip())
        if whole:
            return _lookup(event, whole.group(1))
        return PLACEHOLDER.sub(lambda match: _as_text(_lookup(event, match.group(1))), template)
    return template


def _lookup(event: dict, key: str):
    value = event.get(key)
    return "" if value is None else value


def _as_text(value) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def parse_template(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw or not str(raw).strip():
        raise ValueError("JSON 模板是空的")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 模板格式不對：{exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError("JSON 模板最外層要是物件")
    return data


def parse_headers(raw) -> dict:
    if not raw or (isinstance(raw, str) and not raw.strip()):
        return {}
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Header JSON 格式不對：{exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError("Header 要是 JSON 物件")
    return {str(key): str(value) for key, value in data.items()}


class Forwarder:
    def send(self, rule: dict, event: dict) -> dict:
        body = render_template(rule.get("template") or {}, event)
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        headers.update(rule.get("headers") or {})
        url = (rule.get("url") or "").strip()
        method = (rule.get("method") or "POST").upper()
        if method not in ("POST", "PUT"):
            method = "POST"
        timeout = float(rule.get("timeoutSec") or 5)
        result = {
            "ruleId": rule.get("id"),
            "ruleName": rule.get("name") or "",
            "url": url,
            "plate": event.get("plate") or "",
            "eventId": event.get("id") or "",
            "body": body,
            "ok": False,
            "status": 0,
            "error": "",
            "attempt": 0,
        }
        if not url.startswith(("http://", "https://")):
            result["error"] = "網址要以 http:// 或 https:// 開頭"
            return result

        last_error = ""
        for attempt in range(1, 4):
            result["attempt"] = attempt
            try:
                response = httpx.request(method, url, content=payload, headers=headers, timeout=timeout)
            except httpx.HTTPError as exc:
                last_error = str(exc)
                if attempt < 3:
                    time.sleep(attempt)
                    continue
                break
            result["status"] = response.status_code
            if 200 <= response.status_code < 300:
                result["ok"] = True
                result["error"] = ""
                return result
            last_error = f"HTTP {response.status_code}"
            if response.status_code < 500 and response.status_code not in (408, 429):
                break
            if attempt < 3:
                time.sleep(attempt)
        result["error"] = last_error or "送出失敗"
        return result
