"""分析功能登錄表。新增分析：在任一模組寫一個函式並加上 @register("名稱")。"""

REGISTRY = {}


def register(name, help_text=""):
    def deco(func):
        REGISTRY[name] = func
        func.help_text = help_text or (func.__doc__ or "").strip().splitlines()[0]
        return func
    return deco


def load_all():
    from . import iv, tseries, field  # noqa: F401  (匯入即登錄)
    return REGISTRY
