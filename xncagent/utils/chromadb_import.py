def disable_overrides_type_hint_check() -> None:
    """避免 chromadb 导入时在 Windows 上栈溢出。

    chromadb 0.5 用 overrides 的 @override 核对方法签名，内部会调用
    typing.get_type_hints()。Where 这类注解会互相引用，Python 3.11
    比较它们时会无限递归。Windows 上进程直接被杀掉，报
    "Windows fatal exception: access violation"，测试还没开始跑。

    让类型提示检查直接返回 None 后，overrides 会跳过注解比对，
    参数名是否一致仍然会检查。
    """
    import overrides.signature as signature

    signature._get_type_hints = lambda _callable: None
