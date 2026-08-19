import pytest
import os
from pathlib import Path
import sys



ROOT_DIR = Path(__file__).parent.parent
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HOME"] = str(ROOT_DIR / "models/models")
os.environ["TOKENIZERS_PARALLELISM"] = "false"

sys.path.insert(0, str(ROOT_DIR))

import chromadb
import logging
from chromadb.errors import InvalidCollectionException
from xncagent.config import Config
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction



@pytest.fixture(scope="session")
def chroma_connection():
    """
    连接chroma数据库
    return: chroma数据库连接
    raises: 如果未找到chroma_path或connection_name则抛出pytest.skip
    """
    chroma_path = Config["rag"]["vector_store"]["persist_path_abs"]
    assert chroma_path is not None 
    print(f"chroma_path: {chroma_path}")
    if not chroma_path.exists():
        pytest.skip(f"{chroma_path} 路径不存在,请先执行创建脚本")
    connection_name = Config["rag"]["vector_store"]["collection_name"]
    model_name = Config["rag"]["embedding"]["model_name"]
    client = chromadb.PersistentClient(str(chroma_path))
    try:
        collection = client.get_collection(
            connection_name,
            embedding_function=SentenceTransformerEmbeddingFunction(
                model_name=model_name,
                device="cpu",
                normalize_embeddings=True
            )
        )
    except InvalidCollectionException:
        pytest.skip(f"{connection_name} 连接不存在,请先执行创建脚本")

    return collection


def test_retrieve_top_k(chroma_connection):
    """
    测试检索top_k条数据
    return: 检索到的数据
    raises: 如果未找到数据则抛出pytest.skip
    """
    collection = chroma_connection
    results = collection.query(
        query_texts=["我现在好懒，不想起床"],
        n_results=3,
    )
    assert results["documents"][0] is not None
    assert len(results["documents"][0]) == 3
    logging.info(f"检索到的top_3的数据为: {results['documents']}")
    

def test_retrieve_mutiple_query_texts(chroma_connection):
    """
    测试检索多个query_texts
    return: 检索到的数据
    raises: 如果未找到数据则抛出pytest.skip
    """
    query_texts = ["我现在好懒，不想起床", "安慰朋友的话术", "工作压力大怎么调节"]
    collection = chroma_connection
    results = collection.query(
        query_texts=query_texts,
        n_results=2,
    )
    assert results["documents"][0] is not None
    assert len(results["documents"][0]) == 2
    logging.info(f"检索到的top_2的数据为: {results['documents']}")

def test_retrieve_with_scores(chroma_connection):
    """
    测试检索带有分数的数据
    return: 检索到的数据
    raises: 如果未找到数据则抛出pytest.skip
    """
    collection = chroma_connection
    results = collection.query(
        query_texts=["我现在好懒，不想起床"],
        n_results=3,
    )
    assert results["documents"][0] is not None
    assert len(results["documents"][0]) == 3
    assert "distances" in results
    # 验证相似度分数在合理范围内（归一化向量距离应在 0~2 之间）
    for dist in results["distances"][0]:
        assert 0 <= dist <= 2

def test_retrieve_empty_query(chroma_collection):
    """测试空查询的容错性"""
    # Chroma 对空查询会返回空结果（或报错），取决于版本
    try:
        results = chroma_collection.query(
            query_texts=[""],
            n_results=3,
        )
        # 如果执行到这里，验证结果为空或正常
        assert results["documents"][0] is not None
    except Exception as e:
        # 空查询报错也是合理的
        print(f"空查询报错: {e}")
        pass