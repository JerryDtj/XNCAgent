"""历史数据向量回填：把 PG 里已有的消息/会话摘要灌进 Chroma。

背景：embedding 是异步任务后来才加的，早期消息没落过向量，导致语义搜索
只能查到最近几条。本脚本把 chat_messages / chat_session_summaries 全量
补进 Chroma，metadata 格式与 save_user_msg / save_session_summary 完全一致。

幂等：chroma upsert 按 msg_{id} / summary_{session_id} 覆盖写，重复执行安全。

用法（服务器上，venv 内）：
    cd /opt/xnc/XNCAgent && .venv/bin/python scripts/backfill_chroma.py
"""
import sys

from xncagent.rag.index_store import init_chroma_client, init_embedding_model
from xncagent.rdbms.postgres import get_cursor, init_pool
from xncagent.utils.logger import logger

_BATCH = 32


def _fetch_messages():
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, session_id, user_id, role, content
            FROM chat_messages
            WHERE content IS NOT NULL AND btrim(content) <> ''
            ORDER BY id
            """
        )
        return cur.fetchall()


def _fetch_summaries():
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT session_id, user_id, summary
            FROM chat_session_summaries
            WHERE summary IS NOT NULL AND btrim(summary) <> ''
            ORDER BY session_id
            """
        )
        return cur.fetchall()


def backfill_messages(model, client) -> int:
    rows = _fetch_messages()
    if not rows:
        logger.info("chat_messages 无可回填数据")
        return 0
    collection = client.get_or_create_collection(name="chat_history")
    done = 0
    for i in range(0, len(rows), _BATCH):
        batch = rows[i : i + _BATCH]
        texts = [r["content"] for r in batch]
        embeddings = model.get_text_embedding_batch(texts)
        collection.upsert(
            ids=[f"msg_{r['id']}" for r in batch],
            embeddings=embeddings,
            documents=texts,
            metadatas=[
                {
                    "user_id": str(r["user_id"]),
                    "session_id": r["session_id"],
                    "message_id": r["id"],
                    "role": r["role"],
                }
                for r in batch
            ],
        )
        done += len(batch)
        logger.info(f"[backfill] chat_history 进度 {done}/{len(rows)}")
    return done


def backfill_summaries(model, client) -> int:
    rows = _fetch_summaries()
    if not rows:
        logger.info("chat_session_summaries 无可回填数据")
        return 0
    collection = client.get_or_create_collection(name="chat_summaries")
    done = 0
    for i in range(0, len(rows), _BATCH):
        batch = rows[i : i + _BATCH]
        texts = [r["summary"] for r in batch]
        embeddings = model.get_text_embedding_batch(texts)
        collection.upsert(
            ids=[f"summary_{r['session_id']}" for r in batch],
            embeddings=embeddings,
            documents=texts,
            metadatas=[
                {
                    "user_id": str(r["user_id"]),
                    "session_id": r["session_id"],
                }
                for r in batch
            ],
        )
        done += len(batch)
        logger.info(f"[backfill] chat_summaries 进度 {done}/{len(rows)}")
    return done


def main() -> int:
    init_pool()
    model = init_embedding_model()
    client = init_chroma_client()
    n_msg = backfill_messages(model, client)
    n_sum = backfill_summaries(model, client)
    logger.info(f"[backfill] 完成：消息 {n_msg} 条，摘要 {n_sum} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
