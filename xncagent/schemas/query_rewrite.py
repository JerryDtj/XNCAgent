from typing import Literal
from pydantic import BaseModel, Field, field_validator

class RewriterQuestionResponse(BaseModel):
    rewritten_query: str = Field(description="改写后的完整问题")
    confidence: float = Field(ge=0, le=1, description="模型自报置信度")
    scene: Literal["挨骂兜底", "安慰话术", "懒懒不想动", "职场吐槽", "捧哏金句", "无关闲聊"] = Field(description="场景")
    emotion_alert: bool = Field(description="极度负面情绪预警")
    # 缺字段时用空串。没有默认值的话，旧输出会校验失败，整轮掉进降级通道。
    recall_query: str = Field(default="", description="跨会话检索词；不需要回忆时为空串")

    @field_validator("recall_query", mode="before")
    @classmethod
    def _blank_recall_query(cls, value: object) -> object:
        if value is None:
            return ""
        return value