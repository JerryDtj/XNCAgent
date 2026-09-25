from functools import lru_cache

from openai import OpenAI
from xncagent.utils.exceptions import BizException
from xncagent.schemas.query_rewrite import RewriterQuestionResponse
from xncagent.llm.config import settings, BASE_DIR
from pathlib import Path
import yaml

_client = OpenAI(api_key=settings.agictor_api_key,base_url=settings.agictor_base_url,)

@lru_cache(maxsize=1)
def load_system_prompt() -> str:
    raw = Path(BASE_DIR / "prompts" / "query_rewrite.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    return data["system_prompt"].strip()

SCENE_WHITELIST = ["挨骂兜底", "安慰话术", "懒懒不想动", "捧哏金句", "职场吐槽"]
def rewrite_query(query: str, history: str = "") -> RewriterQuestionResponse:
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
    completion = _client.chat.completions.parse(
        model=settings.model_name,
        messages=[
            {"role": "system","content": load_system_prompt()},
            {
                "role": "user",
                "content": f"## 历史对话\n{history}\n\n## 用户输入\n{query}"
            }
        ],
        response_format=RewriterQuestionResponse,
        temperature=0.0,
    )
    result = completion.choices[0].message.parsed
    if result is None:
        raise BizException("LLM调用失败，result为空")
    return RewriterQuestionResponse.model_validate_json(result)
  
