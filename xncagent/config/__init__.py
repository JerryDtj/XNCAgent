"""
配置模块，用于加载配置文件
"""


from pathlib import Path

import yaml

# 获取当前目录 /Users/dengtianjiao/PycharmProjects/XNCAgent/xncagent/config
_current_dir = Path(__file__).parent
# 获取项目根目录 /Users/dengtianjiao/PycharmProjects/XNCAgent
_project_dir = _current_dir.parent.parent

def load_config(name: str) -> dict:
    """
    加载配置文件
    :param name: 配置文件名称
    :return: 配置字典
    """
    with open(_current_dir / name, "r", encoding="utf-8") as file:
        Config = yaml.safe_load(file)
        return Config
    
rag_config = load_config('rag_config.yaml')
llm_config = load_config('llm_config.yaml')
logger_config = load_config('configuration.yaml')


# 将相对路径转为绝对路径（供脚本和运行时使用）
rag_config["vector_store"]["docs_dir_abs"] = _project_dir / rag_config["vector_store"]["docs_dir"]
rag_config["vector_store"]["persist_dir_abs"] = _project_dir / rag_config["vector_store"]["persist_dir"]

# 合并配置,方便后续使用
Config = {
    'rag': rag_config,
    'llm': llm_config,
    'logger': logger_config,
}
 