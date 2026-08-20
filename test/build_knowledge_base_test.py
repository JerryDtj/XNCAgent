
import pytest
import os
import logging
from pathlib import Path
import sys



ROOT_DIR = Path(__file__).parent.parent
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HOME"] = str(ROOT_DIR / "models/models")
os.environ["TOKENIZERS_PARALLELISM"] = "false"
sys.path.insert(0, str(ROOT_DIR))


from script.build_knowledge_base import get_index
from llama_index.core.schema import NodeWithScore
from llama_index.postprocessor.flag_embedding_reranker import FlagEmbeddingReranker

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def knowledge_index():
    """
    连接chroma数据库
    return: chroma数据库连接
    raises: 如果未找到chroma_path或connection_name则抛出pytest.skip
    """
    return get_index()


def test_retrieve_top_k(knowledge_index):
    """
    测试检索top_k条数据
    return: 检索到的数据
    raises: 如果未找到数据则抛出pytest.skip
    """
    retrieve = knowledge_index.as_retriever(similarity_top_k=3)
    nodes = retrieve.retrieve("我现在好懒，不想起床")
    reranker = FlagEmbeddingReranker(
        model="BAAI/bge-reranker-base",
        top_n=3,
    )
    nodes = reranker.postprocess_nodes(nodes, query_str="我现在好懒，不想起床")
    logger.info(f"检索到的top_3的数据为: {nodes}")

    assert len(nodes) == 3
    for node in nodes:
        assert isinstance(node, NodeWithScore)
        assert node.score is not None
        logger.info(f"score={node.score:.4f}, text={node.text[:50]}...")    

def test_retrieve_with_scores(knowledge_index):
    """
    测试检索带有分数的数据
    return: 检索到的数据
    raises: 如果未找到数据则抛出pytest.skip
    """
    retrieve = knowledge_index.as_retriever(similarity_top_k=3)
    nodes = retrieve.retrieve("工作压力大")
    logger.info(f"检索到的top_3的数据为: {nodes}")

    for node in nodes:
        # 验证 LlamaIndex 的溯源能力（Chroma 原生 API 做不到）
        assert hasattr(node, "metadata")
        logger.info(f"来源: {node.metadata.get('file_name', 'unknown')}")
        logger.info(f"内容: {node.text[:80]}...")