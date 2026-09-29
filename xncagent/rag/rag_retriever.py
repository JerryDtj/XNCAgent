
from llama_index.core.vector_stores.types import FilterOperator, MetadataFilter, MetadataFilters
from xncagent.rag.index_store import get_index
from llama_index.postprocessor.flag_embedding_reranker import FlagEmbeddingReranker
from xncagent.utils.logger import logger
from xncagent.rag.index_store import init_embedding_model, init_chroma_client


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
    embedding = model.encode(texts,normalize_embeddings=True).tolist()

    collection.upsert(
        ids = [d["id"] for d in doc],
        embeddings = embedding,
        texts = texts,
        metadatas = [
            {
                "user_id": user_id,
                "session_id": session_id,
                "message_id": d["mid"],
                "role": d["role"],
            } 
            for d in doc
        ],
    )
