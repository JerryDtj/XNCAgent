from typing import Literal
from pydantic import BaseModel, Field

class RewriterQuestionResponse(BaseModel):
    rewritten_query: str = Field(description="改写后的完整问题")
    confidence: float = Field(ge=0, le=1, description="模型自报置信度")
    scene: Literal["挨骂兜底", "安慰", "懒懒不想动", "职场吐槽", "捧哏金句", "无关闲聊"] = Field(description="场景")
    emotion_alert: bool = Field(description="极度负面情绪预警")