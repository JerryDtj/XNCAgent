import os
from pathlib import Path

# 必须在导入 HuggingFace / chromadb 之前设置，否则模型会下到默认缓存，
# 且 Chroma 0.5 在 Windows + Python 3.11 上导入时会栈溢出。
_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
os.environ["HF_HOME"] = str(_PROJECT_DIR / "models" / "models")
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

from xncagent.rag.chromadb_import import disable_overrides_type_hint_check

disable_overrides_type_hint_check()

import chromadb
from llama_index.core import Settings, VectorStoreIndex
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore

from xncagent.config import Config
from xncagent.utils.logger import logger

vector_config = Config["rag"]["vector_store"]
COLLECTION_NAME = vector_config.get("collection_name", "xiaoxizi_knowledge")
VECTOR_PERSIST_PATH = vector_config.get("persist_path_abs", Path("chroma_db"))

embedding_config = Config["rag"]["embedding"]
MODEL_NAME = embedding_config.get("model_name", "BAAI/bge-small-zh-v1.5")
CHUNK_SIZE = embedding_config.get("chunk_size", 512)
CHUNK_OVERLAP = embedding_config.get("chunk_overlap", 50)
BATCH_SIZE = embedding_config.get("batch_size", 8)

_embed_model = None
_chroma_client = None


def init_embedding_model() -> HuggingFaceEmbedding:
    """
    初始化嵌入模型
    :return: 嵌入模型
    """
    global _embed_model
    if _embed_model is None:
        _embed_model = HuggingFaceEmbedding(
            model_name=MODEL_NAME,
            device="cpu",
        )
        Settings.embed_model = _embed_model
        logger.info(f"嵌入模型初始化完成: {MODEL_NAME}")
    return _embed_model


def init_chroma_client() -> chromadb.Client:
    """
    初始化Chroma客户端
    :return: Chroma客户端
    """
    global _chroma_client
    if _chroma_client is None:
        VECTOR_PERSIST_PATH.mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=str(VECTOR_PERSIST_PATH))
        logger.info(f"Chroma客户端初始化完成: {VECTOR_PERSIST_PATH}")
    return _chroma_client


def get_index() -> VectorStoreIndex:
    """
    获取向量索引
    :return: 向量索引
    """
    init_embedding_model()

    client = init_chroma_client()
    connection = client.get_collection(name=COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=connection)
    return VectorStoreIndex.from_vector_store(vector_store=vector_store)
