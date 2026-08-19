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

from llama_index.core import SimpleDirectoryReader
from llama_index.core.node_parser import SentenceSplitter

import chromadb
from chromadb.errors import InvalidCollectionException
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from tqdm import tqdm

vector_config = Config['rag']['vector_store']
COLLECTION_NAME = vector_config.get('collection_name',"xiaoxizi_knowledge")
DOCS_DIR = vector_config.get('docs_dir_abs',Path("doc/话术"))
VECTOR_PERSIST_PATH = vector_config.get('persist_path_abs',Path("chroma_db"))

embedding_config = Config['rag']['embedding']
MODEL_NAME = embedding_config.get('model_name',"sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
EMBEDDING_PERSIST_PATH = embedding_config.get('persist_path_abs',Path("models/embedding"))
CHUNK_SIZE = embedding_config.get('chunk_size',512)
CHUNK_OVERLAP = embedding_config.get('chunk_overlap',50)
BATCH_SIZE = embedding_config.get('batch_size',8)

logger_config = Config['system']['logger']
LOG_LEVEL = logger_config.get('level',"INFO")
LOG_FORMAT = logger_config.get('console_format',"<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>")
LOG_FILE = logger_config.get('filename',"logs/xncagent.log")
LOG_MAX_SIZE = logger_config.get('max_size',"10 MB")



def log_memory_usage():
    """记录内存使用情况(MB)"""
    try:
        import psutil
        process = psutil.Process(os.getpid())
        memory_usage = process.memory_info().rss / 1024 / 1024
        logger.info(f"内存使用情况: {memory_usage:.2f} MB")
        return memory_usage
    except ImportError as e:
        logger.error(f"导入psutil失败: {e}")
        return None
    except Exception as e:
        logger.error(f"记录内存使用情况失败: {e}")
        return None


def load_docs():
    """
    加载所有文档
    Args:
        docs_dir: 文档目录
    Returns:
        docs: 文档列表
    """
    logger.info(f"开始从{DOCS_DIR}处加载文档: 递归加载所有 .md 文件")

    if not DOCS_DIR.exists():
        logger.error(f"文档目录不存在: {DOCS_DIR}")
        raise FileNotFoundError(f"文档目录不存在: {DOCS_DIR}")
    
    reader = SimpleDirectoryReader(
        input_dir=str(DOCS_DIR),
        recursive=True,
        required_exts=[".md"],
        encoding="utf-8",
    )
    docs = reader.load_data()
    if not docs:
        logger.error(f"没有找到任何文档,请检查文档目录是否正确: {DOCS_DIR}")
        return []
    logger.info(f"加载完成,共加载 {len(docs)} 个文档")
    log_memory_usage()
    return docs

def split_docs(docs: list):
    """
    对文档进行分块
    Args:
        docs: 文档列表
        chunk_size: 分块大小
        chunk_overlap: 分块重叠
    Returns:
        nodes: 分块后的节点列表
    """
    logger.info(f"开始对文档进行分块: 分块大小: {CHUNK_SIZE}, 重叠: {CHUNK_OVERLAP}")
    splitter = SentenceSplitter(chunk_size=CHUNK_SIZE,chunk_overlap=CHUNK_OVERLAP)
    nodes = splitter.get_nodes_from_documents(docs)
    logger.info(f"分块完成,共分块 {len(nodes)} 个节点")
    log_memory_usage()
    return nodes

def _should_skip_confirmation():
    """
    检查是否需要跳过安全防护
    """
    return "--force" in sys.argv

def _confirm_or_exit(message: str):
    """
    确认或退出
    """
    if _should_skip_confirmation():
        logger.info("🔓 强制模式已启用（--force），跳过交互确认")
        return

    #  非交互式终端 -> 直接过,走保底备份删除逻辑
    # 交互式终端 -> 用户确认
    logger.warning("=" * 50)
    logger.warning("⚠️  即将删除现有 Collection 并完全重建！")
    logger.warning(f"目标持久化目录: {VECTOR_PERSIST_PATH}")
    logger.warning(f"目标 Collection: {COLLECTION_NAME}")
    logger.warning("=" * 50)
    confirm = input("请输入 'YES' 确认执行（其他任意字符取消）: ")
    if confirm.lower() != "yes":
        logger.info("用户取消操作")
        sys.exit(0)
    
def init_chroma_db():
    """
    初始化向量数据库chroma:如果集合已存在,则走阻塞流程,确认后重命名集合,并创建新集合,不存在直接创建
    Args:
        collection_name: 集合名称
    Returns:
        chroma_db: 向量数据库
    """
    logger.info(f"开始初始化向量数据库chroma: 集合名称: {COLLECTION_NAME}")
    VECTOR_PERSIST_PATH.mkdir(parents=True,exist_ok=True)
    client = chromadb.PersistentClient(path=str(VECTOR_PERSIST_PATH))

    try:
        collection = client.get_collection(name=COLLECTION_NAME)
        logger.info(f"集合 {COLLECTION_NAME} 已存在,走阻塞流程")
        _confirm_or_exit(f"集合 {COLLECTION_NAME} 已存在,是否继续？")
        new_collection_name = f"{COLLECTION_NAME}_backup_{time.strftime('%Y-%m-%d_%H-%M-%S')}"
        collection.modify(name=new_collection_name)
        logger.info(f"集合 {COLLECTION_NAME} 已重命名为 {new_collection_name}")
    except InvalidCollectionException:
        logger.info(f"集合 {COLLECTION_NAME} 不存在,将创建新集合")
    
    # 使用 normalize_embeddings=True 确保向量归一化
    enbedding_fn = SentenceTransformerEmbeddingFunction(model_name=MODEL_NAME, device="cpu", normalize_embeddings=True)
    collection = client.create_collection(
        name=str(COLLECTION_NAME),
        embedding_function=enbedding_fn,
        metadata={
            "description": "xnc_agent 话术知识库",
            "embedding_model": MODEL_NAME,
            "chunk_size": CHUNK_SIZE,
            "created_at": datetime.datetime.now().isoformat(),
        }
    )
    logger.info(f"集合 {COLLECTION_NAME} 创建成功")
    logger.info(f"向量数据库chroma初始化完成")
    log_memory_usage()
    return collection

def insert_chunks(nodes: list,collection ):
    """
    插入分块
    Args:
        nodes: 分块后的节点列表
        collection: 集合
        model: 嵌入模型
        batch_size: 批次大小
    Returns:
        insert_count: 插入数量
    """
    total = len(nodes)
    logger.info(f"开始插入分块: 节点数量: {total}")
    if not nodes or total == 0:
        logger.error(f"没有找到任何节点,请检查文档是否正确: {DOCS_DIR}")
        return 0
    insert_count = 0
    with tqdm(nodes,desc="插入分块",total=total) as pbar:
        for i in range(0,total,BATCH_SIZE):
            batch_nodes = nodes[i:i+BATCH_SIZE]
            try:
                collection.add(
                    documents = [n.text for n in batch_nodes],
                    ids = [n.node_id for n in batch_nodes],
                    metadatas = [n.metadata for n in batch_nodes],
                )
                insert_count += len(batch_nodes)
                pbar.update(len(batch_nodes))
            except Exception as e:
                logger.error(f"插入分块失败: {e}")
                continue
    return insert_count

def main():
    start_time = time.time()
    logger.info("=" * 50)
    logger.info("知识库构建启动")
    log_memory_usage()

    try:
        # 1.加载所有文档
        docs = load_docs()
        # 2.对文档进行分块
        nodes = split_docs(docs)
        # 3.初始化向量数据库chroma
        collection = init_chroma_db()
        # 4.按照2核4G的配置，批量嵌入和存储
        insert_count = insert_chunks(nodes,collection)
        # 5.释放资源
        gc.collect()
        Spend_time = time.time() - start_time
        logger.info("=" * 50)
        logger.info(f"构建知识库完成，耗时: {Spend_time:.2f}秒")
        logger.info(f"文档目录: {DOCS_DIR}")
        logger.info(f"embedding持久化目录: {EMBEDDING_PERSIST_PATH}")
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