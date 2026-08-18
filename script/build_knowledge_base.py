
import os
from pathlib import Path
import sys

import chromadb
from llama_index.core.indices import vector_store
from llama_index.vector_stores.chroma import ChromaVectorStore

from xncagent.utils import logger

# 配置环境变量
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HOME"] = Config['rag']['embedding']['persist_path']


from llama_index.core import Document, Settings, SimpleDirectoryReader, StorageContext, settings

from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from xncagent.config import Config




# 嵌入模型名称
config_rag = Config['rag']['vector_store']
DOCS_DIR = config_rag.get('docs_dir_abs')
PERSIST_DIR = config_rag.get('persist_dir_abs')
COLLECTION_NAME = config_rag.get('collection_name')

config_embedding = Config['rag']['embedding']
MODLE_NAME = config_embedding.get('model_name')
CHUNK_SIZE = config_embedding.get('chunk_size')
CHUNK_OVERLAP = config_embedding.get('chunk_overlap')
BATCH_SIZE = config_embedding.get('batch_size')

def init_embedding_model(model_name: str, batch_size: int):
    """
    初始化嵌入模型
    Args:
        model_name: 模型名称
        batch_size: 批量大小
    Returns:
        embed_model: 嵌入模型对象
    Raises:
        Exception: 初始化嵌入模型失败
    """
    try:
        logger.info(f"初始化嵌入模型{model_name}")
        embed_model = HuggingFaceEmbedding(
            model_name=model_name,
            embed_batch_size=BATCH_SIZE,
            device="cpu"
        )
        #快速失败,预热,自检模型是否可用
        embed_model.aget_query_embedding(text="Hello, world!")
        logger.info(f"嵌入模型{model_name}初始化成功")
        return embed_model
    except Exception as e:
        logger.error(f"初始化嵌入模型{model_name}失败: {e}")
        sys.exit(1)


def load_documents(docs_dir: str):
    """
    加载文档
    Args:
        docs_dir: 文档目录
    Returns:
        documents: 文档列表
    Raises:
        Exception: 加载文档失败
    """
    try:
        logger.info(f"加载文档,文档目录: {docs_dir}")
        reader = SimpleDirectoryReader(
            input_dir=docs_dir,
            recursive=True,
            required_exts=[".md", ".txt"],
            encoding="utf-8",
        )
        documents = reader.load_data()
        if len(documents) == 0:
            logger.error(f"加载文档失败,文档目录: {docs_dir}")
            sys.exit(0)
        logger.info(f"加载文档成功,文档数量: {len(documents)}")
        return documents
    except Exception as e:
        logger.error(f"加载文档失败: {e}")
        sys.exit(1)


def split_documents(documents: list[Document], chunk_size: int, chunk_overlap: int):
    """
    分割文档,生成节点块
    Args:
        documents: 文档列表
        chunk_size: 分块大小
        chunk_overlap: 分块重叠
    Returns:
        nodes: 节点块列表
    Raises:
        Exception: 分割文档失败
    """
    try:
        logger.info(f"分割文档,文档数量: {len(documents)}, 分块大小: {chunk_size}, 分块重叠: {chunk_overlap}")
        text_splitter = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        nodes = text_splitter.get_nodes_from_documents(documents)
        logger.info(f"分割文档成功,共生成: {len(nodes)}个节点块")
        return nodes
    except Exception as e:
        logger.error(f"分割文档失败: {e}")
        sys.exit(1)

def init_chroma_knowledge_base(collection_name: str,persist_path: Path):
    """
    初始化Chroma知识库
    Args:
        collection_name: 知识库名称
        persist_path: 持久化路径
    Returns:
        chroma_client: Chroma知识库客户端对象
    Raises:
        Exception: 初始化Chroma知识库失败
    """
    logger.info(f"初始化Chroma知识库,知识库名称: {collection_name}")
    try:
        persist_path.mkdir(parents=True, exist_ok=True)
        chroma_client = chromadb.PersistentClient(path=str(persist_path))
        try:
            chroma_client.delete_collection(collection_name=collection_name)
            logger.info(f"已删除旧知识库: {collection_name}")
        except Exception as e:
            pass
        chroma_collection = chroma_client.get_or_create_collection(name=collection_name)
        logger.info(f"初始化Chroma知识库成功,知识库名称: {collection_name}")
        vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        return vector_store,storage_context
        
    except Exception as e:
        logger.error(f"初始化Chroma知识库失败: {e}")
        sys.exit(1)


def main():
    """
    构建知识库脚本
    """

    # 1.初始化本地小模型
    Settings.embed_model = init_embedding_model(MODLE_NAME, BATCH_SIZE)
    # 2.加载文档
    documents = load_documents(str(DOCS_DIR))
    # 3.初始化文本分割器
    Settings.text_splitter = split_documents(documents, CHUNK_SIZE, CHUNK_OVERLAP)
    # 4.Chroma 知识库初始化

    # 5.添加文档到知识库

    # 6.清理

    init_embedding_model(MODLE_NAME, BATCH_SIZE)

    Settings.embed_model = HuggingFaceEmbedding(
        model_name=MODLE_NAME,
        embed_batch_size=BATCH_SIZE,
        device="cpu"
    )


    settings.text_spltter = SentenceSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP
    )

    if not os.path.exists(DOCS_DIR):
        print(f"文档目录{DOCS_DIR}不存在，请检查配置文件")
        return
    
    

    

if __name__ == "__main__":
    main()