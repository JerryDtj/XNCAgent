#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Time    : 2026/08/19 8:31:00
# @Author  : Jerry
# @File    : build_knowledge_base.py
# @Description: 构建知识库:将 doc/话术/ 下的所有 .md 文件分块并存入 Chroma。仅需运行一次，后续更新知识库时重新运行即可。

"""
- 从 rag_config.yaml 和 configuration.yaml 读取配置
- 功能拆分为独立函数（加载模型、加载文档、分块、入库等）
- 2核4G适配,分批处理防止内存溢出 (batch_size=8)
- 集中式日志管理（从 xncagent.utils.logger 导入）
- 精简异常处理，统一由 main 捕获
"""


import psutil
import datetime
import os
import sys
import gc
import time
from pathlib import Path

os.environ["HF_HOME"] = str(Path(__file__).parent.parent / "models/models")
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["TOKENZERS_PARALLELISM"] = "false"

from xncagent.config import Config
from xncagent.utils.logger import logger

from llama_index.core import (
    VectorStoreIndex,
    StorageContext,
    Settings,
    SimpleDirectoryReader,
)
from llama_index.core.node_parser import SentenceSplitter
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

import chromadb
from chromadb.errors import InvalidCollectionException

from tqdm import tqdm

vector_config = Config['rag']['vector_store']
COLLECTION_NAME = vector_config.get('collection_name',"xiaoxizi_knowledge")
DOCS_DIR = vector_config.get('docs_dir_abs',Path("doc/话术"))
VECTOR_PERSIST_PATH = vector_config.get('persist_path_abs',Path("chroma_db"))

embedding_config = Config['rag']['embedding']
MODEL_NAME = embedding_config.get('model_name',"sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
CHUNK_SIZE = embedding_config.get('chunk_size',512)
CHUNK_OVERLAP = embedding_config.get('chunk_overlap',50)
BATCH_SIZE = embedding_config.get('batch_size',8)

logger_config = Config['system']['logger']
LOG_LEVEL = logger_config.get('level',"INFO")
LOG_FORMAT = logger_config.get('console_format',"<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>")
LOG_FILE = logger_config.get('filename',"logs/xncagent.log")
LOG_MAX_SIZE = logger_config.get('max_size',"10 MB")


_enbed_model = None
_chroma_client = None

def _init_embedding_model() -> HuggingFaceEmbedding:
    """
    初始化嵌入模型
    :return: 嵌入模型
    """
    global _enbed_model
    if _enbed_model is None:
        _enbed_model = HuggingFaceEmbedding(
            model_name=MODEL_NAME,
            device= "cpu",
        )
        Settings.embed_model = _enbed_model
        logger.info(f"嵌入模型初始化完成: {MODEL_NAME}")
        return _enbed_model

def _init_chroma_client() -> chromadb.Client:
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

def log_memory_usage() -> float | None:
    try:
        process = psutil.Process(os.getpid())
        memory_mb = process.memory_info().rss / 1024 / 1024
        logger.info(f"内存使用: {memory_mb:.2f} MB")
        return memory_mb
    except Exception as e:
        logger.warning(f"内存监控失败: {e}")
        return None

def load_docs() -> list:
    """
    加载所有文档
    :param docs_dir: 文档目录
    :return: 文档列表
    """
    if not DOCS_DIR.exists():
        logger.error(f"文档目录不存在: {DOCS_DIR}")
        raise FileNotFoundError(f"文档目录不存在: {DOCS_DIR}")

    reader = SimpleDirectoryReader(
        input_dir=str(DOCS_DIR),
        required_exts=[".md"],
        recursive=True,
        encoding="utf-8",
    )
    docs = reader.load_data()
    logger.info(f"加载完成, 共 {len(docs)} 个文档")
    log_memory_usage()
    if not docs:
        logger.error(f"文档目录下没有文档: {DOCS_DIR}")
        raise FileNotFoundError(f"文档目录下没有文档: {DOCS_DIR}")
    return docs

def split_docs(docs: list) -> list:
    """
    对文档进行分块
    :param docs: 文档列表
    :return: 分块后的节点列表
    """
    logger.info(f"开始分块: size={CHUNK_SIZE}, chunk_overlap = {CHUNK_OVERLAP}")
    splitter = SentenceSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    nodes = splitter.get_nodes_from_documents(docs)
    logger.info(f"文档分块完成，分块数量: {len(nodes)}")
    log_memory_usage()
    return nodes


def _backup_existing_collection(client):
    """
    备份已存在的集合
    :param client: Chroma客户端
    :return: None
    """
    try:
        connection = client.get_collection(name=COLLECTION_NAME)
        logger.info(f"集合: {COLLECTION_NAME}已存在")   
        if '--force' not in sys.argv:
            confrim = input(f"请输入 'YES' 确认重建: ")
            if confrim != 'YES':
                logger.info(f"用户取消重建")
                exit(0)
        new_name = f"{COLLECTION_NAME}_backup_{time.strftime('%Y-%m-%d_%H-%M-%S')}"
        connection.modify(name=new_name)
        logger.info(f"已备份为: {new_name}")
    except InvalidCollectionException as e:
        logger.info(f"集合 {COLLECTION_NAME} 不存在, 将创建新集合")

def init_chroma_store():
    """
    初始化Chroma向量存储
    :return: Chroma向量存储
    """
    client = _init_chroma_client()
    _backup_existing_collection(client)

    collection = client.create_collection(
        name=COLLECTION_NAME,
        metadata={
            "description": "xnc_agent 话术知识库",
            "embedding_model": MODEL_NAME,
            "chunk_size": CHUNK_SIZE,
            "created_at": datetime.datetime.now().isoformat(),
        },
    )
    logger.info(f"集合: {COLLECTION_NAME} 创建完成")
    return ChromaVectorStore(chroma_collection=collection)

def build_index(nodes: list, vector_store: ChromaVectorStore) -> VectorStoreIndex:
    """
    构建向量索引
    :param nodes: 节点列表
    :param vector_store: 向量存储
    :return: 向量索引
    """
    _init_embedding_model()

    logger.info(f"开始构建向量索引")
    index = VectorStoreIndex(
        nodes=nodes,
        storage_context=StorageContext.from_defaults(vector_store=vector_store),
        show_progress=True,
    )
    logger.info(f"构建完成, 共 {len(index.docstore.docs)} 个节点")
    log_memory_usage()
    return index

def get_index() -> VectorStoreIndex:
    """
    获取向量索引
    :return: 向量索引
    """
    _init_embedding_model()

    client = _init_chroma_client()
    connection = client.get_collection(name=COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=connection)
    log_memory_usage()
    return VectorStoreIndex.from_vector_store(vector_store=vector_store)


def main():
    start_time = time.time()
    logger.info("=" * 50)
    logger.info("知识库构建启动")
    try:
        # 1.加载所有文档
        docs = load_docs()
        # 2.对文档进行分块
        nodes = split_docs(docs)
        # 3.初始化向量数据库chroma
        vector_store = init_chroma_store()
        # 4.按照2核4G的配置，批量嵌入和存储
        index = build_index(nodes,vector_store)
        # 5.释放资源
        gc.collect()

        Spend_time = time.time() - start_time
        logger.info("=" * 50)
        logger.info(f"构建知识库完成，耗时: {Spend_time:.2f}秒")
        logger.info(f"文档目录: {DOCS_DIR}")
        logger.info(f"分块大小: {CHUNK_SIZE}, 重叠: {CHUNK_OVERLAP}")
        logger.info(f"批次大小: {BATCH_SIZE}")
        logger.info(f"chroma持久化目录: {VECTOR_PERSIST_PATH}")
        logger.info(f"Collection: {COLLECTION_NAME}")
        logger.info(f"嵌入模型: {MODEL_NAME}")
        logger.info("=" * 50)

    except KeyboardInterrupt:
        logger.error(f"构建知识库中断")
        exit(0)
    except Exception as e:
        logger.error(f"构建知识库失败: {e}")
        exit(1)
    
   

if __name__ == "__main__":
    main()