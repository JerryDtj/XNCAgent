
from llama_index.core.vector_stores.types import FilterOperator, MetadataFilter, MetadataFilters
from xncagent.rag.index_store import get_index
from llama_index.postprocessor.flag_embedding_reranker import FlagEmbeddingReranker
from llama_index.core.schema import NodeWithScore
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