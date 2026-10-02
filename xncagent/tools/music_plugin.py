"""场景音乐插件：按 scene 从曲库随机选歌。"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Optional

import yaml

from xncagent.tools.plugin import PluginResult
from xncagent.utils.logger import logger

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "music_library.yaml"

_NOTICE = "（将使用网络流量播放）"
_REASON_TMPL = "主子聊到这茬，奴才斗胆配个曲儿——《{title}》"


class MusicPlugin:
    """play_music：场景音乐。曲库损坏时仍注册，run 返回 ok=False。"""

    name = "play_music"
    description = "按场景从曲库随机选一首歌，返回播放信息"
    parameters = {
        "type": "object",
        "properties": {
            "scene": {
                "type": "string",
                "description": "当前对话场景名",
            },
        },
        "required": ["scene"],
    }

    def __init__(self) -> None:
        self._scene_music: dict[str, list[dict[str, Any]]] = {}
        self._probability: dict[str, float] = {"default": 0.3}
        self._reason_templates: list[str] = []
        self._load_error: str = ""
        # 进程内最近播放：(session_key -> 上次 title)，避免同一会话连续重复同一首
        self._last_by_session: dict[str, str] = {}
        self._load_library()

    def _load_library(self) -> None:
        """启动时加载并校验曲库；失败只记错误，不抛。"""
        try:
            if not _CONFIG_PATH.is_file():
                self._load_error = f"曲库文件不存在: {_CONFIG_PATH}"
                logger.error(f"[play_music] {self._load_error}")
                return
            with _CONFIG_PATH.open("r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
            if not isinstance(raw, dict):
                self._load_error = "曲库 YAML 根节点不是对象"
                logger.error(f"[play_music] {self._load_error}")
                return
            prob = raw.get("probability") or {}
            if isinstance(prob, dict):
                self._probability = {
                    str(k): float(v) for k, v in prob.items() if v is not None
                }
                if "default" not in self._probability:
                    self._probability["default"] = 0.3
            scene_music = raw.get("scene_music") or {}
            if not isinstance(scene_music, dict):
                self._load_error = "scene_music 不是对象"
                logger.error(f"[play_music] {self._load_error}")
                return
            parsed: dict[str, list[dict[str, Any]]] = {}
            for scene, tracks in scene_music.items():
                if not isinstance(tracks, list):
                    self._load_error = f"场景 [{scene}] 的歌曲列表不是数组"
                    logger.error(f"[play_music] {self._load_error}")
                    return
                cleaned: list[dict[str, Any]] = []
                for i, track in enumerate(tracks):
                    if not isinstance(track, dict):
                        self._load_error = f"场景 [{scene}] 第 {i} 首不是对象"
                        logger.error(f"[play_music] {self._load_error}")
                        return
                    has_file = bool(track.get("file"))
                    has_url = bool(track.get("url"))
                    if has_file == has_url:
                        self._load_error = (
                            f"场景 [{scene}] 第 {i} 首须 file/url 二选一"
                        )
                        logger.error(f"[play_music] {self._load_error}")
                        return
                    if not track.get("title"):
                        self._load_error = f"场景 [{scene}] 第 {i} 首缺少 title"
                        logger.error(f"[play_music] {self._load_error}")
                        return
                    cleaned.append(track)
                parsed[str(scene)] = cleaned
            self._scene_music = parsed
            self._reason_templates = _parse_reason_templates(raw.get("reason_templates"))
            logger.info(
                f"[play_music] 曲库已加载 scenes={list(parsed.keys())} "
                f"probability={self._probability} "
                f"reason_templates={len(self._reason_templates)}"
            )
        except Exception as exc:
            self._load_error = f"曲库加载失败: {exc}"
            logger.exception(f"[play_music] {self._load_error}")

    @property
    def load_failed(self) -> bool:
        """曲库启动加载是否失败（文件缺失/损坏）。失败时 run 返回 ok=False。"""
        return bool(self._load_error)

    def scene_configured(self, scene: str) -> bool:
        """该场景是否配置了至少一首歌。曲库损坏时除「无关闲聊」外仍返回 True，交给 run 报错。"""
        if self._load_error:
            return scene != "无关闲聊"
        return bool(self._scene_music.get(scene))

    def probability_for(self, scene: str) -> float:
        """取场景概率，无覆盖则用 default。曲库损坏时仍用已读到的/默认概率。"""
        if scene in self._probability and scene != "default":
            return float(self._probability[scene])
        return float(self._probability.get("default", 0.3))

    def run(self, args: dict) -> PluginResult:
        if self._load_error:
            return PluginResult(ok=False, error=self._load_error)

        scene = (args or {}).get("scene")
        if not scene or not isinstance(scene, str):
            return PluginResult(ok=False, error="缺少参数 scene")

        candidates = list(self._scene_music.get(scene) or [])
        if not candidates:
            return PluginResult(ok=False, error=f"场景未配置音乐: {scene}")

        session_key = str((args or {}).get("session_key") or "")
        last_title = self._last_by_session.get(session_key) if session_key else None
        pool = candidates
        if last_title and len(candidates) > 1:
            filtered = [t for t in candidates if t.get("title") != last_title]
            if filtered:
                pool = filtered

        track = random.choice(pool)
        title = str(track["title"])
        if track.get("file"):
            rel = str(track["file"]).lstrip("/")
            url = f"/music/{rel}"
        else:
            url = str(track["url"])

        if session_key:
            self._last_by_session[session_key] = title

        return PluginResult(
            ok=True,
            data={
                "title": title,
                "url": url,
                "scene": scene,
                "reason": self._render_reason(title),
                "notice": _NOTICE,
            },
        )

    def _render_reason(self, title: str) -> str:
        """从 reason_templates 随机抽一条；未配置或为空时用固定模板。"""
        templates = self._reason_templates or [_REASON_TMPL]
        return random.choice(templates).format(title=title)


def _parse_reason_templates(raw: Any) -> list[str]:
    """顶层 reason_templates：只保留非空字符串。缺失、非列表或滤完为空都交给回退模板。"""
    if not isinstance(raw, list):
        return []
    return [item.strip() for item in raw if isinstance(item, str) and item.strip()]


# 模块级单例，供 Agent 查配置与调 run
music_plugin = MusicPlugin()
