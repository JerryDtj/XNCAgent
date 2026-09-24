from typing import Optional
from xncagent.utils.response import success
from xncagent.llm.llm_task import llm_task

def task_check(query: str) -> Optional[dict]:
    # 查询是否直接命中场景
    scene = short_query_map(query)
    if scene is not None:
        # 直接命中，把场景信息和query送入rag查询

        # 查询结果凭借prompt送入llm推理
        return success(result)
    else:
        # 未命中，检查是否需要改写

        # 需要改写，把query送入llm进行改写，返回改写后的query以及对应的业务场景
        # 改写后的query送入rag查询
        # 查询结果凭借prompt送入llm推理
        return success(result)
      
            # 不需要改写，直接送到本地模型拿场景

            # 把场景信息和query送入rag查询
            # 查询结果凭借prompt送入llm推理
            
            


