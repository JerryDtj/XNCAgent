"""笑话插件：调用 apihz 笑话接口。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

from xncagent.tools.plugin import PluginResult
from xncagent.utils.logger import logger

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(_PROJECT_DIR / ".env")

# apihz 笑话接口（https://cn.apihz.cn）
_API_URL = "https://cn.apihz.cn/api/yiyan/xiaohua.php"
_DEFAULT_ID = "10011925"


class JokePlugin:
    """get_joke：从 apihz 拉一条笑话。"""

    name = "get_joke"
    description = "获取一条笑话"
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def run(self, args: dict) -> PluginResult:
        api_key = os.getenv("apihz_api_key") or os.getenv("APIHZ_API_KEY") or ""
        api_id = os.getenv("apihz_id") or os.getenv("APIHZ_ID") or _DEFAULT_ID
        if not api_key:
            return PluginResult(ok=False, error="未配置 apihz_api_key")

        query = urllib.parse.urlencode({"id": api_id, "key": api_key})
        url = f"{_API_URL}?{query}"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=8) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            err = f"apihz HTTP {exc.code}"
            logger.warning(f"[get_joke] {err}")
            return PluginResult(ok=False, error=err)
        except Exception as exc:
            err = f"apihz 请求失败: {exc}"
            logger.warning(f"[get_joke] {err}")
            return PluginResult(ok=False, error=err)

        joke = _parse_joke(body)
        if not joke:
            return PluginResult(ok=False, error=f"apihz 返回无法解析: {body[:200]}")
        return PluginResult(ok=True, data={"joke": joke})


def _parse_joke(body: str) -> str:
    """兼容 JSON / 纯文本两种返回。"""
    text = (body or "").strip()
    if not text:
        return ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(data, dict):
        for key in ("msg", "data", "content", "text"):
            val = data.get(key)
            if isinstance(val, str) and val.strip() and key != "msg":
                return val.strip()
        # 部分接口成功时 msg 就是笑话正文
        code = data.get("code")
        msg = data.get("msg")
        if isinstance(msg, str) and msg.strip():
            if code in (200, "200", None) and msg not in ("成功", "ok", "OK"):
                return msg.strip()
        if isinstance(data.get("data"), str):
            return data["data"].strip()
    if isinstance(data, str):
        return data.strip()
    return ""


joke_plugin = JokePlugin()
