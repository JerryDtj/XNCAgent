import json
from functools import lru_cache

from openai import OpenAI
from pydantic import ValidationError

from xncagent.config.prompts import load_system_prompt
from xncagent.llm.config import settings
from xncagent.scene_matcher import load_rag_whitelist
from xncagent.schemas.query_rewrite import RewriterQuestionResponse
from xncagent.utils.exceptions import BizException
from xncagent.utils.logger import logger

# RAG 检索白名单：5 个场景（不含兜底"无关闲聊"），与 scene_desc.yaml 一处定义
SCENE_WHITELIST = list(load_rag_whitelist())


def _load_json_object(content: str) -> dict:
    """从模型原文里取出第一个 JSON 对象。提示词已要求只回 JSON，这里兼容外层说明和代码块。"""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    if start < 0:
        raise BizException("LLM 未返回 JSON")
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise BizException("LLM 返回的 JSON 无法解析") from exc
    if not isinstance(data, dict):
        raise BizException("LLM 返回的 JSON 不是对象")
    return data

@lru_cache(maxsize=1)
def _get_client() -> OpenAI:
    """
    惰性创建 OpenAI 客户端。

    不在模块导入时初始化：没有 API Key 的环境（如本地跑评测、降级通道演练）
    也能 import 本模块，只有真正调用 LLM 时才要求凭证。
    """
    return OpenAI(api_key=settings.require_api_key(), base_url=settings.agictor_base_url)   

def query_rewrite(query: str, history: str = "") -> RewriterQuestionResponse:
    """
    一次 LLM 调用，同时完成改写与场景识别。

    :param query: 用户原始输入
    :param history: 预检命中时传入的最近 N 轮对话（预检未命中时为空串）
    :return: {
        "rewritten_query": str,   # 改写后的完整问题；自包含时原样返回
        "scene": str,             # 6 值之一：5 个场景白名单 + "无关闲聊"
        "emotion_alert": bool,    # 极度负面情绪预警（树洞双保险的第一道）
        "confidence": float,      # 模型自报置信度，只记日志不参与决策
    }
    调用失败抛异常，由调用方兜底（返回兜底话术 + 记日志）。
    """
    logger.info(f"[LLM] 发往服务商 model={settings.check_model_name} stream=False")
    completion = _get_client().chat.completions.create(
        model=settings.check_model_name,
        messages=[
            {"role": "system", "content": load_system_prompt("understand_query")},
            {
                "role": "user",
                "content": f"## 历史对话\n{history}\n\n## 用户输入\n{query}",
            },
        ],
        temperature=0.0,
    )
    content = completion.choices[0].message.content or ""
    try:
        return RewriterQuestionResponse.model_validate(_load_json_object(content))
    except ValidationError as exc:
        raise BizException("LLM 返回的字段不符合约定") from exc
  
