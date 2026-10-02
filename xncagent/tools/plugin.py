"""插件协议：对齐 MCP tools/list + tools/call 语义，一期进程内实现。"""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class PluginResult:
    """插件执行结果。error 只进日志，不进用户消息。"""

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@runtime_checkable
class Plugin(Protocol):
    """插件协议：name / description / parameters 对齐 MCP tool 描述。"""

    name: str
    description: str
    parameters: dict

    def run(self, args: dict) -> PluginResult: ...
