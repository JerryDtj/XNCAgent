
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from llama_index.core.schema import NodeWithScore, TextNode
from llama_index.core.vector_stores.types import FilterOperator, MetadataFilter, MetadataFilters
from llama_index.postprocessor.flag_embedding_reranker import FlagEmbeddingReranker

from xncagent.rag.index_store import get_index, init_chroma_client, init_embedding_model
from xncagent.rdbms.message_repo import get_messages_by_ids
from xncagent.rdbms.session_repo import get_sessions_by_ids
from xncagent.utils.logger import logger


def retrieve_knowledge(query: str, scene: str) -> str:
    """
    检索知识库，返回知识库中的相关内容
    :param query: 用户输入的查询
    :param scene: 场景
    :return: 知识库中的相关内容
    """
    filter = MetadataFilters(filters=[MetadataFilter(key="scene", value=scene, operator=FilterOperator.EQ)])
    
    retrieve = get_index().as_retriever(similarity_top_k=5, filter=filter)
    nodes = retrieve.retrieve(query)

    if not nodes:
        logger.info(f"没有检索到相关内容")
        return ""

    # 开始对指定结果再次排序
    reranker = FlagEmbeddingReranker(
        model="BAAI/bge-reranker-base",
        top_n=3,
    )
    nodes = reranker.postprocess_nodes(nodes, query_str=query)
    logger.info(f"场景 [{scene}] 检索到的top_3的数据为: {nodes}")
    return "/n/n".join([node.text.strip() for node in nodes if node.text.strip()])

def save_user_msg(session_id: int, user_id: int, user_msg_id: int, assistant_msg_id: int, query: str, answer: str) -> None:
    """
    把一轮的两条消息 embedding 进 chroma 的 chat_history collection。
    user_id 存字符串（chroma metadata filter 用），session_id / message_id 存 int。
    """
    model = init_embedding_model()
    client = init_chroma_client()
    collection = client.get_or_create_collection(name="chat_history")
    
    doc = [
        {"id":f"msg_{user_msg_id}","text":query, "role":"user", "mid":user_msg_id},
        {"id":f"msg_{assistant_msg_id}","text":answer, "role":"assistant", "mid":assistant_msg_id},
    ]

    texts = [d["text"] for d in doc]
    embedding = model.get_text_embedding_batch(texts)

    collection.upsert(
        ids = [d["id"] for d in doc],
        embeddings = embedding,
        documents = texts,
        metadatas = [
            {
                "user_id": str(user_id),
                "session_id": session_id,
                "message_id": d["mid"],
                "role": d["role"],
            } 
            for d in doc
        ],
    )


_EMPTY_REPLY = "奴才不记得主子提过这茬，要不再说细点儿？"
_HISTORY_COLLECTION = "chat_history"
_SUMMARY_COLLECTION = "chat_summaries"


def save_session_summary(session_id: int, user_id: int, summary: str) -> None:
    """
    把会话摘要嵌入 chat_summaries。
    metadata 与 search_sessions 对齐：user_id 存字符串，session_id 存 int。
    先按 session_id 删掉旧向量，再用稳定 id 写入，保证一会话一条。
    """
    model = init_embedding_model()
    client = init_chroma_client()
    collection = client.get_or_create_collection(name=_SUMMARY_COLLECTION)
    found = collection.get(where={"session_id": session_id})
    old_ids = found.get("ids") or []
    if old_ids:
        collection.delete(ids=old_ids)
    embedding = model.get_text_embedding_batch([summary])
    collection.upsert(
        ids=[f"summary_{session_id}"],
        embeddings=embedding,
        documents=[summary],
        metadatas=[{
            "user_id": str(user_id),
            "session_id": session_id,
        }],
    )


_TOP_K = 20
_RESULT_LIMIT = 10
_SNIPPET_LEN = 80
_MIN_RERANK_SCORE = 0.0


def _collection_names(client) -> set[str]:
    names: set[str] = set()
    for item in client.list_collections():
        if isinstance(item, str):
            names.add(item)
        else:
            name = getattr(item, "name", None)
            if name:
                names.add(name)
    return names


def _query_one(client, name: str, embedding: list[float], where: dict, top_k: int) -> dict:
    return client.get_collection(name).query(
        query_embeddings=[embedding],
        n_results=top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )


def _query_collections(
    client,
    names: list[str],
    embedding: list[float],
    where: dict,
    top_k: int,
) -> dict[str, dict]:
    if not names:
        return {}
    if len(names) == 1:
        return {names[0]: _query_one(client, names[0], embedding, where, top_k)}
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futures = {
            pool.submit(_query_one, client, name, embedding, where, top_k): name
            for name in names
        }
        for future, name in futures.items():
            out[name] = future.result()
    return out


def retrieve_user_vectors(
    query: str,
    user_id: int,
    collections: tuple[str, ...] = (_HISTORY_COLLECTION, _SUMMARY_COLLECTION),
    top_k: int = _TOP_K,
) -> list[NodeWithScore]:
    """
    按 user_id 字符串过滤，在指定 collection 上做向量检索。
    搜索接口与跨会话召回共用这一份；集合不存在或没有命中时返回空列表。
    """
    model = init_embedding_model()
    client = init_chroma_client()
    embedding = model.get_query_embedding(query)
    where = {"user_id": str(user_id)}
    names = _collection_names(client)
    targets = [name for name in collections if name in names]
    if not targets:
        return []
    queried = _query_collections(client, targets, embedding, where, top_k)
    candidates: list[NodeWithScore] = []
    cursor = 0
    for name in targets:
        level = "message" if name == _HISTORY_COLLECTION else "session"
        batch = _nodes_from_query(level, queried[name], cursor)
        cursor += len(batch)
        candidates.extend(batch)
    return candidates


def _nodes_from_query(level: str, result: dict, start: int) -> list[NodeWithScore]:
    docs = (result.get("documents") or [[]])[0] or []
    metas = (result.get("metadatas") or [[]])[0] or []
    dists = (result.get("distances") or [[]])[0] or []
    nodes: list[NodeWithScore] = []
    for offset, text in enumerate(docs):
        meta = dict(metas[offset] or {}) if offset < len(metas) else {}
        if meta.get("session_id") is None:
            continue
        text = text or ""
        if not text.strip():
            continue
        # id_ 只在内存里带着 level，不写回 chroma，也不新增 metadata 字段
        node = TextNode(text=text, id_=f"{level}:{start + offset}", metadata=meta)
        score = None
        if offset < len(dists) and dists[offset] is not None:
            score = -float(dists[offset])
        nodes.append(NodeWithScore(node=node, score=score))
    return nodes


def _snippet(text: str) -> str:
    return text.strip()[:_SNIPPET_LEN]


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def search_sessions(query: str, user_id: Optional[int]) -> dict:
    """
    跨会话语义搜索：消息级（chat_history）+ 会话摘要级（chat_summaries，存在才查）。
    返回 {"items": [...], "reply"?: "..."}
    - 匿名 user_id=None：直接空 items + 固定话术，不进检索
    - 两个 collection 都带 where={"user_id": str(user_id)}，和 save_user_msg 写入的字符串对齐
    """
    if user_id is None:
        return {"items": [], "reply": _EMPTY_REPLY}

    candidates = retrieve_user_vectors(query, user_id)
    if not candidates:
        return {"items": [], "reply": _EMPTY_REPLY}

    reranker = FlagEmbeddingReranker(
        model="BAAI/bge-reranker-base",
        top_n=len(candidates),
    )
    ranked = reranker.postprocess_nodes(candidates, query_str=query)

    best: dict[int, tuple[float, NodeWithScore]] = {}
    for item in ranked:
        session_id = int(item.node.metadata["session_id"])
        score = item.score if item.score is not None else float("-inf")
        if score <= _MIN_RERANK_SCORE:
            continue
        prev = best.get(session_id)
        if prev is None or score > prev[0]:
            best[session_id] = (score, item)

    ordered = sorted(best.values(), key=lambda pair: pair[0], reverse=True)[:_RESULT_LIMIT]
    session_ids = [int(item.node.metadata["session_id"]) for _, item in ordered]
    message_ids = [
        int(item.node.metadata["message_id"])
        for _, item in ordered
        if item.node.id_.startswith("message:") and item.node.metadata.get("message_id") is not None
    ]
    sessions = get_sessions_by_ids(session_ids)
    messages = get_messages_by_ids(message_ids)

    items = []
    for _, item in ordered:
        meta = item.node.metadata
        level = "message" if item.node.id_.startswith("message:") else "session"
        session_id = int(meta["session_id"])
        session_row = sessions.get(session_id)
        if not session_row:
            continue
        if level == "message":
            message_id = int(meta["message_id"])
            created_at = (messages.get(message_id) or {}).get("created_at")
        else:
            message_id = None
            created_at = session_row.get("created_at")
        items.append({
            "level": level,
            "session_id": session_id,
            "session_title": session_row.get("title") or "",
            "message_id": message_id,
            "snippet": _snippet(item.node.get_content()),
            "created_at": _iso(created_at),
        })

    if not items:
        return {"items": [], "reply": _EMPTY_REPLY}
    return {"items": items}


def delete_session_vectors(session_id: int) -> None:
    """按 session_id metadata 删掉 chat_history / chat_summaries 里该会话的向量。集合不存在则跳过。"""
    client = init_chroma_client()
    names = _collection_names(client)
    where = {"session_id": session_id}
    for name in (_HISTORY_COLLECTION, _SUMMARY_COLLECTION):
        if name not in names:
            continue
        client.get_collection(name).delete(where=where)
        logger.info(f"已删除 collection={name} session_id={session_id} 的向量")
