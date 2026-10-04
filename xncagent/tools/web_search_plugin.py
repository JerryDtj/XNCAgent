"""联网搜索插件。本期走博查，Tavily 只留切换口子。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml

from xncagent.tools.plugin import PluginResult
from xncagent.utils.logger import logger

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "web_search.yaml"


@dataclass
class SearchHit:
    title: str
    snippet: str
    url: str


class SearchProvider(Protocol):
    def search(self, query: str, count: int) -> list[SearchHit]: ...


class BochaProvider:
    """POST {base_url}。yaml 里的 base_url 已含 /web-search，不再另接路径。"""

    def __init__(self, base_url: str, api_key: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_config(cls, cfg: dict, timeout_seconds: float) -> "BochaProvider":
        bocha = cfg.get("bocha") or {}
        return cls(
            base_url=str(bocha.get("base_url") or ""),
            api_key=os.getenv("BOCHA_API_KEY") or "",
            timeout_seconds=timeout_seconds,
        )

    def search(self, query: str, count: int) -> list[SearchHit]:
        if not self.api_key:
            raise RuntimeError("未配置 BOCHA_API_KEY")
        if not self.base_url:
            raise RuntimeError("未配置 bocha.base_url")
        body = json.dumps(
            {
                "query": query,
                "freshness": "noLimit",
                "summary": True,
                "count": count,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            self.base_url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"博查 HTTP {exc.code} {detail}") from exc
        except Exception as exc:
            raise RuntimeError(f"博查请求失败: {exc}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("博查返回不是 JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("博查返回不是对象")
        code = payload.get("code")
        if code not in (None, 200, "200"):
            raise RuntimeError(f"博查返回 code={code} msg={payload.get('msg')}")
        return _hits_from_bocha(payload)


def _hits_from_bocha(payload: dict) -> list[SearchHit]:
    """取 data.webPages.value[] 的 name / snippet / url。

    设计 §3.3 约定的结构。summary=true 时条目里可能另有 summary，本期不用。
    大模型参考资料块本期不解析。
    本环境没有 BOCHA_API_KEY，未能打一次真实响应；若线上键路径不同，
    以那次响应为准改这里，并把真实键路径补进本注释。
    """
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return []
    pages = data.get("webPages") or {}
    if not isinstance(pages, dict):
        return []
    values = pages.get("value") or []
    if not isinstance(values, list):
        return []
    hits: list[SearchHit] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        title = str(item.get("name") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title and not snippet and not url:
            continue
        hits.append(SearchHit(title=title, snippet=snippet, url=url))
    return hits


class TavilyProvider:
    """换供应商时改配置里的 provider 即可。search 本期不实现。"""

    def __init__(self, base_url: str, api_key: str, timeout_seconds: float) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_config(cls, cfg: dict, timeout_seconds: float) -> "TavilyProvider":
        tavily = cfg.get("tavily") or {}
        return cls(
            base_url=str(tavily.get("base_url") or ""),
            api_key=os.getenv("TAVILY_API_KEY") or "",
            timeout_seconds=timeout_seconds,
        )

    def search(self, query: str, count: int) -> list[SearchHit]:
        raise NotImplementedError("Tavily 留待后续接入")


def _load_web_search_config() -> dict:
    if not _CONFIG_PATH.is_file():
        raise RuntimeError(f"配置文件不存在: {_CONFIG_PATH}")
    with _CONFIG_PATH.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict) or not isinstance(raw.get("web_search"), dict):
        raise RuntimeError("web_search.yaml 缺少 web_search 节点")
    return raw["web_search"]


def _build_provider(cfg: dict, timeout_seconds: float) -> SearchProvider:
    name = str(cfg.get("provider") or "")
    if name == "bocha":
        return BochaProvider.from_config(cfg, timeout_seconds)
    if name == "tavily":
        return TavilyProvider.from_config(cfg, timeout_seconds)
    raise RuntimeError(f"未知 provider: {name}")


class WebSearchPlugin:
    """name=web_search。HTTP 失败只放进 PluginResult.error，不往外抛。"""

    name = "web_search"
    description = "联网搜索网页，返回标题、摘要和链接"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "用户原始问题"},
            "count": {"type": "integer", "description": "返回条数"},
        },
        "required": ["query"],
    }

    def __init__(self) -> None:
        self.provider_name = ""
        self.count = 8
        self.timeout_seconds = 10.0
        self._provider: SearchProvider | None = None
        self._init_error = ""
        try:
            cfg = _load_web_search_config()
            self.provider_name = str(cfg.get("provider") or "")
            self.count = int(cfg.get("count") or 8)
            self.timeout_seconds = float(cfg.get("timeout_seconds") or 10)
            self._provider = _build_provider(cfg, self.timeout_seconds)
        except Exception as exc:
            self._init_error = str(exc)
            logger.error(f"[web_search] provider 未配置: {exc}")

    def run(self, args: dict) -> PluginResult:
        if self._init_error or self._provider is None:
            return PluginResult(ok=False, error=self._init_error or "provider 未配置")
        query = str((args or {}).get("query") or "")
        try:
            count = int((args or {}).get("count") or self.count)
        except (TypeError, ValueError):
            count = self.count
        try:
            hits = self._provider.search(query, count)
        except Exception as exc:
            return PluginResult(ok=False, error=str(exc))
        return PluginResult(
            ok=True,
            data={
                "hits": [
                    {"title": hit.title, "snippet": hit.snippet, "url": hit.url}
                    for hit in hits
                ]
            },
        )


web_search_plugin = WebSearchPlugin()
