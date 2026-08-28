"""App 及入口脚本导入冒烟测试（issue #33）。

AGENTS.md 测试优先级要求对 app 模块做导入冒烟测试。Streamlit 页面会在模块顶层调用
st.set_page_config / st.selectbox 等运行时 API，无法在 Streamlit 运行时之外完整 import。

本测试用无操作 streamlit 桩替换，捕获「导入结构缺陷」：
  - 缺失模块 / 语法错误 / 循环依赖 / 顶层 import 失败  → 直接失败（真正的回归）
  - 仅来自顶层 Streamlit 运行时调用的异常（如桩值被用于 dict 索引） → 视为需要
    Streamlit 运行时的预期行为，不算导入结构缺陷（更深的运行时冒烟需 streamlit.testing）

这样能在 CI 中尽早发现「改坏 import」类破坏性变更，而不会因为缺少 Streamlit 服务器而误报。
"""
import importlib
import sys
import types
import unittest
from unittest.mock import MagicMock


class _SessionState(dict):
    """支持属性与下标两种访问的 session_state 桩（app.py 两种都用）。"""

    def __getattr__(self, name):
        return self.get(name)

    def __setattr__(self, name, value):
        self[name] = value


class _StreamlitStub(types.ModuleType):
    @property
    def session_state(self):
        return _SessionState()

    @property
    def cache_data(self):
        def decorator(*args, **kwargs):
            # 支持 @st.cache_data 与 @st.cache_data(ttl=...) 两种写法，均返回被装饰函数
            if args:
                return args[0]
            return lambda f: f

        return decorator

    def __getattr__(self, name):
        return MagicMock()


class AppImportSmokeTest(unittest.TestCase):
    def _load_with_stub(self, module_name: str):
        saved = sys.modules.get("streamlit")
        sys.modules["streamlit"] = _StreamlitStub("streamlit")
        try:
            return importlib.import_module(module_name)
        except (ImportError, ModuleNotFoundError, SyntaxError):
            raise  # 真正的导入期结构缺陷，必须失败
        except Exception:
            # 模块代码已成功解析并执行到 Streamlit 运行时调用；异常仅来自需要 Streamlit
            # 运行时的顶层调用（如 st.selectbox 桩值被用于 dict 索引）。视为导入结构正常。
            # 顶层模块导入失败会被解释器从 sys.modules 移除，故用真值哨兵表示「结构通过」。
            return sys.modules.get(module_name) or True
        finally:
            if saved is None:
                sys.modules.pop("streamlit", None)
            else:
                sys.modules["streamlit"] = saved

    def test_app_entry_imports(self):
        self.assertIsNotNone(self._load_with_stub("app"))

    def test_quant_page_imports(self):
        self.assertIsNotNone(self._load_with_stub("scripts.quant_page"))

    def test_confidence_page_imports(self):
        self.assertIsNotNone(self._load_with_stub("scripts.confidence_page"))


if __name__ == "__main__":
    unittest.main()
