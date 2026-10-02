"""进程内插件注册表。二期可换成真实 MCP Server，调用方零改动。"""

from typing import Optional

from xncagent.tools.plugin import Plugin
from xncagent.utils.logger import logger

_REGISTRY: dict[str, Plugin] = {}


def register(plugin: Plugin) -> None:
    """注册插件。同名覆盖并记 WARN。"""
    if plugin.name in _REGISTRY:
        logger.warning(f"[tools] 插件重名覆盖: {plugin.name}")
    _REGISTRY[plugin.name] = plugin
    logger.info(f"[tools] 已注册插件: {plugin.name}")


def get(name: str) -> Optional[Plugin]:
    """按名取插件，未注册返回 None。"""
    return _REGISTRY.get(name)


def list_tools() -> list[dict]:
    """返回 MCP tools/list 风格结构。"""
    return [
        {
            "name": p.name,
            "description": p.description,
            "inputSchema": p.parameters,
        }
        for p in _REGISTRY.values()
    ]
