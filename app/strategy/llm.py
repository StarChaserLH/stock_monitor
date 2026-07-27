"""
DeepSeek-V4-Pro API 客户端。

用于将自然语言策略描述转换为可执行的 Python 策略函数。
支持错误自动修正，最多重试 3 次。
"""

import logging
import re
from typing import Optional

from openai import OpenAI

from app.strategy.prompts import (
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    ERROR_CORRECTION_PROMPT,
)

logger = logging.getLogger(__name__)


class LLMStrategyGenerator:
    """基于 DeepSeek API 的策略代码生成器。

    Usage:
        gen = LLMStrategyGenerator(config)
        code = gen.generate("当5日均线上穿20日均线时买入")
    """

    def __init__(self, config):
        """
        Args:
            config: LLMConfig 实例。
        """
        self._config = config
        self._client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    def generate(self, description: str) -> dict:
        """将自然语言描述转换为策略代码。

        包含最多 3 次自动修正循环。

        Returns:
            {"code": str, "desc": str} — 策略代码和 LLM 生成的简介。
        """
        raw = self._call_llm(USER_PROMPT_TEMPLATE.format(description=description))
        gen_desc = self._extract_desc(raw)
        code = self._extract_code(raw)

        for attempt in range(self._config.max_retries):
            error_msg = self._validate_code(code)
            if error_msg is None:
                logger.info("策略代码生成成功并通过验证")
                return {"code": code, "desc": gen_desc or description[:60]}

            logger.warning(f"策略代码验证失败 (尝试 {attempt + 1}/{self._config.max_retries}): {error_msg}")

            correction_prompt = ERROR_CORRECTION_PROMPT.format(
                error=error_msg,
                description=description,
                code=code,
            )
            code = self._call_llm(correction_prompt)
            code = self._extract_code(code)

        raise RuntimeError(
            f"策略代码生成失败，已超过最大重试次数 ({self._config.max_retries})"
        )

    @staticmethod
    def _extract_desc(text: str) -> str:
        lines = text.strip().split("\n")
        if lines and not lines[0].startswith(("def ", "import ", "from ", "#", "```")):
            return lines[0].strip()
        return ""

    def test_generated_code(self, code: str) -> Optional[str]:
        """测试生成的代码是否可以正常执行。

        Args:
            code: 策略函数源代码。

        Returns:
            错误信息字符串，成功返回 None。
        """
        import pandas as pd
        import numpy as np

        # 构造测试数据
        dates = pd.date_range("2025-01-01", periods=60, freq="D")
        test_data = pd.DataFrame({
            "open": np.random.randn(60).cumsum() + 10,
            "high": np.random.randn(60).cumsum() + 11,
            "low": np.random.randn(60).cumsum() + 9,
            "close": np.random.randn(60).cumsum() + 10,
            "volume": np.abs(np.random.randn(60)) * 1e6 + 1e6,
            "amount": np.abs(np.random.randn(60)) * 1e8,
            "pct_change": np.random.randn(60) * 0.02,
        }, index=dates)

        test_context = {
            "positions": {},
            "cash": 100000.0,
            "signals": [],
            "holdings": {},
        }

        try:
            # 安全沙箱执行
            local_ns: dict = {}
            exec(code, {"pd": pd, "np": np, "__builtins__": _safe_builtins()}, local_ns)
            func = local_ns.get("strategy")
            if func is None:
                return "代码中未找到 strategy 函数"
            result = func(test_context, test_data)
            if not isinstance(result, dict):
                return f"返回值类型错误: 期望 dict，实际 {type(result).__name__}"
            if "action" not in result:
                return "返回值缺少 'action' 字段"
            if result["action"] not in ("buy", "sell", "hold"):
                return f"无效的 action 值: {result['action']}"
            return None
        except Exception as e:
            return f"执行错误: {type(e).__name__}: {e}"

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _call_llm(self, user_prompt: str) -> str:
        """调用 DeepSeek API（含超时保护）。"""
        try:
            response = self._client.chat.completions.create(
                model=self._config.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self._config.temperature,
                max_tokens=self._config.max_tokens,
                timeout=60,
            )
            content = response.choices[0].message.content
            return content or ""
        except Exception as e:
            logger.error(f"DeepSeek API 调用失败: {e}")
            raise RuntimeError(f"LLM 调用失败: {e}") from e

    @staticmethod
    def _extract_code(text: str) -> str:
        """从 LLM 响应中提取纯 Python 代码。

        处理可能包含 Markdown 代码块包装的输出。
        """
        text = text.strip()
        # 尝试提取 ```python ... ``` 代码块
        pattern = r"```(?:python)?\s*\n(.*?)```"
        matches = re.findall(pattern, text, re.DOTALL)
        if matches:
            return matches[0].strip()
        # 没有代码块标记，直接返回
        return text

    @staticmethod
    def _validate_code(code: str) -> Optional[str]:
        """静态验证生成的代码。

        检查语法正确性、函数签名，并阻止危险操作。
        """
        import ast

        if not code or not code.strip():
            return "生成的代码为空"

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return f"语法错误: {e}"

        # 检查是否定义了 strategy 函数
        func_defs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        strategy_func = None
        for func in func_defs:
            if func.name == "strategy":
                strategy_func = func
                break

        if strategy_func is None:
            return "代码中未定义 strategy 函数"

        # 检查函数参数
        args = strategy_func.args
        if len(args.args) != 2:
            return "strategy 函数必须有恰好 2 个参数 (context, data)"

        # 检查是否有禁止的操作（遍历整个 AST，不仅仅是函数体）
        forbidden_calls = {"exec", "eval", "compile", "__import__", "open", "input",
                          "getattr", "setattr", "delattr", "globals", "locals"}
        forbidden_attrs = {"__class__", "__bases__", "__subclasses__", "__mro__",
                          "__globals__", "__dict__", "__builtins__", "__code__",
                          "__closure__", "__func__", "__self__", "__module__"}

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                return f"禁止导入模块: import {node.names[0].name}"
            if isinstance(node, ast.ImportFrom):
                return f"禁止导入模块: from {node.module}"
            if isinstance(node, ast.Global) or isinstance(node, ast.Nonlocal):
                return "禁止使用 global/nonlocal 声明"
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in forbidden_calls:
                        return f"禁止调用函数: {node.func.id}"
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr in forbidden_calls:
                        return f"禁止调用方法: {node.func.attr}"
            if isinstance(node, ast.Attribute):
                if node.attr in forbidden_attrs:
                    return f"禁止访问属性: {node.attr}"

        return None


def _safe_builtins() -> dict:
    """返回受限制的 builtins，禁用危险函数。"""
    import builtins
    safe = {
        "True": True,
        "False": False,
        "None": None,
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "float": float,
        "int": int,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "range": range,
        "round": round,
        "set": set,
        "slice": slice,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "type": type,
        "zip": zip,
        "isinstance": isinstance,
        "hasattr": hasattr,
        "getattr": getattr,
        "print": lambda *a, **k: None,   # 静默 print
    }
    return safe
