"""LLM 策略代码安全沙箱测试。

测试 AST 验证器和安全 builtins 对各种攻击向量的防御能力。
"""

import textwrap

import pytest

from app.strategy.llm import LLMStrategyGenerator, _safe_builtins

# _validate_code 是 LLMStrategyGenerator 的静态方法
_validate_code = LLMStrategyGenerator._validate_code


class TestSafeBuiltins:
    """安全 builtins 测试。"""

    def test_no_eval(self):
        safe = _safe_builtins()
        assert "eval" not in safe
        assert "exec" not in safe
        assert "compile" not in safe
        assert "__import__" not in safe
        assert "open" not in safe

    def test_print_is_silent(self):
        """print 函数被替换为无操作。"""
        safe = _safe_builtins()
        p = safe["print"]
        result = p("should not appear")
        assert result is None

    def test_safe_functions_available(self):
        """安全函数可用。"""
        safe = _safe_builtins()
        assert safe["len"]("abc") == 3
        assert safe["max"]([1, 2, 3]) == 3
        assert safe["sorted"]([3, 1, 2]) == [1, 2, 3]
        assert isinstance(safe["range"](5), range)


class TestASTValidation:
    """AST 静态验证测试。"""

    def test_empty_code(self):
        assert _validate_code("") is not None
        assert _validate_code("   ") is not None

    def test_syntax_error(self):
        code = "def strategy(context, data) return {}"
        err = _validate_code(code)
        assert "语法错误" in err

    def test_no_strategy_function(self):
        code = "def foo(x): pass"
        err = _validate_code(code)
        assert "未定义 strategy" in err

    def test_wrong_arg_count(self):
        code = "def strategy(x): return {'action': 'hold'}"
        err = _validate_code(code)
        assert "2 个参数" in err

    def test_forbidden_exec(self):
        code = textwrap.dedent("""
            def strategy(context, data):
                exec("print(1)")
                return {"action": "hold", "reason": "", "strength": 0}
        """)
        err = _validate_code(code)
        assert err is not None
        assert "禁止调用函数" in err

    def test_forbidden_eval(self):
        code = textwrap.dedent("""
            def strategy(context, data):
                eval("1+1")
                return {"action": "hold", "reason": "", "strength": 0}
        """)
        err = _validate_code(code)
        assert err is not None
        assert "禁止调用函数" in err

    def test_forbidden_import(self):
        code = textwrap.dedent("""
            import os
            def strategy(context, data):
                return {"action": "hold", "reason": "", "strength": 0}
        """)
        err = _validate_code(code)
        assert err is not None
        assert "禁止导入" in err

    def test_forbidden_from_import(self):
        code = textwrap.dedent("""
            from os import system
            def strategy(context, data):
                return {"action": "hold", "reason": "", "strength": 0}
        """)
        err = _validate_code(code)
        assert err is not None
        assert "禁止导入" in err

    def test_forbidden_class_attr(self):
        """测试 __class__ 属性访问被阻止。"""
        code = textwrap.dedent("""
            def strategy(context, data):
                x = "".__class__
                return {"action": "hold", "reason": "", "strength": 0}
        """)
        err = _validate_code(code)
        assert err is not None
        assert "禁止访问属性" in err

    def test_forbidden_subclasses(self):
        """测试 __subclasses__ 属性访问被阻止。"""
        code = textwrap.dedent("""
            def strategy(context, data):
                x = object.__subclasses__()
                return {"action": "hold", "reason": "", "strength": 0}
        """)
        err = _validate_code(code)
        assert err is not None
        assert "禁止访问属性" in err

    def test_forbidden_globals(self):
        """测试 __globals__ 属性访问被阻止。"""
        code = textwrap.dedent("""
            def strategy(context, data):
                x = strategy.__globals__
                return {"action": "hold", "reason": "", "strength": 0}
        """)
        err = _validate_code(code)
        assert err is not None
        assert "禁止访问属性" in err

    def test_forbidden_open(self):
        code = textwrap.dedent("""
            def strategy(context, data):
                f = open("/etc/passwd")
                return {"action": "hold", "reason": "", "strength": 0}
        """)
        err = _validate_code(code)
        assert err is not None
        assert "禁止调用函数" in err

    def test_forbidden_compile(self):
        code = textwrap.dedent("""
            def strategy(context, data):
                compile("1+1", "", "eval")
                return {"action": "hold", "reason": "", "strength": 0}
        """)
        err = _validate_code(code)
        assert err is not None
        assert "禁止调用函数" in err

    def test_global_statement(self):
        """global 声明被阻止。"""
        code = textwrap.dedent("""
            def strategy(context, data):
                global x
                return {"action": "hold", "reason": "", "strength": 0}
        """)
        err = _validate_code(code)
        assert err is not None
        assert "global" in err

    def test_valid_strategy_passes(self):
        """合法策略代码通过验证。"""
        code = textwrap.dedent("""
            def strategy(context, data):
                if data is None or len(data) < 5:
                    return {"action": "hold", "reason": "数据不足", "strength": 0.0}
                close = data['close']
                ma5 = close.rolling(5).mean()
                if close.iloc[-1] > ma5.iloc[-1]:
                    return {"action": "buy", "reason": "价格上穿", "strength": 0.7}
                return {"action": "hold", "reason": "", "strength": 0.0}
        """)
        err = _validate_code(code)
        assert err is None
