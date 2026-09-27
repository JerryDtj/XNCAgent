# -*- coding: utf-8 -*-
# @File    : scene_matcher.py
# @Description: 场景匹配：bge 句向量方案。
#
# 设计见 doc/架构/详细设计/20260926-场景匹配层设计-bge方案.md：
#   - 用户输入与各场景描述（xncagent/config/scene_desc.yaml）算余弦相似度；
#   - top1 分数 >= threshold → 该场景；低于阈值 → fallback_scene（无关闲聊兜底）；
#   - 角色 A：LLM 调用失败时的本地降级通道；
#   - 角色 B：LLM 正常返回时的交叉校验观测（不一致记 WARN 日志）。
#
# 注意：描述口径以 doc/场景定义/ 为权威来源，scene_desc.yaml 是其机器可消费版。

from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml

from xncagent.utils.logger import logger

_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "scene_desc.yaml"
_MODEL_NAME = "BAAI/bge-small-zh-v1.5"


@lru_cache(maxsize=1)
def _load_config() -> dict:
    data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    return {
        "threshold": float(data["threshold"]),
        "fallback": data["fallback_scene"],
        "scenes": dict(data["scenes"]),  # 场景名 -> 描述
    }


@lru_cache(maxsize=1)
def _load_model():
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(_MODEL_NAME, device="cpu")
    logger.info(f"场景匹配模型加载完成: {_MODEL_NAME}")
    return model


# 场景定义目录：判定规则的权威来源（供 LLM prompt 与评测集出题，不进向量原型）
_SCENE_DEF_DIR = Path(__file__).resolve().parent.parent / "doc" / "场景定义"


@lru_cache(maxsize=1)
def _scene_matrix():
    """场景名列表 + 描述向量矩阵（归一化）。"""
    cfg = _load_config()
    model = _load_model()
    names = list(cfg["scenes"].keys())
    embeddings = model.encode(
        [cfg["scenes"][name] for name in names],
        normalize_embeddings=True,
    )
    return names, embeddings


def load_scenes() -> tuple[str, ...]:
    """全部场景枚举（含兜底），供 LLM prompt / 白名单校验使用。"""
    return tuple(_load_config()["scenes"].keys())


def load_rag_whitelist() -> tuple[str, ...]:
    """RAG 检索白名单：全部场景去掉兜底场景。"""
    fallback = _load_config()["fallback"]
    return tuple(s for s in _load_config()["scenes"] if s != fallback)


def match_scene(query: str) -> tuple[str, float]:
    """
    场景匹配：返回 (场景名, top1 分数)。

    top1 分数低于配置阈值时返回 (fallback_scene, top1 分数)。
    模型常驻内存，首次调用加载。

    注：曾实验过多原型方案（doc/场景定义 例句 + max 池化），60 条评测集
    从 46/60 退步到 31/60（例句拉高闲聊相似度、兜底被淹），已回滚。
    例句只用于 LLM prompt 与评测集出题，不进向量原型。
    """
    cfg = _load_config()
    names, embeddings = _scene_matrix()
    query_emb = _load_model().encode([query], normalize_embeddings=True)
    sims = (query_emb @ embeddings.T)[0]
    top_idx = int(np.argmax(sims))
    top_score = float(sims[top_idx])
    if top_score < cfg["threshold"]:
        logger.info(
            f"场景匹配低于阈值({top_score:.3f} < {cfg['threshold']}), "
            f"兜底部景: {cfg['fallback']}"
        )
        return cfg["fallback"], top_score
    return names[top_idx], top_score
