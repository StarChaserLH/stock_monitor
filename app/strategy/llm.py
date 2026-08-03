"""
DeepSeek API 策略代码生成器。

采用 思维链 + 多示例 提示，支持智能错误诊断和自动修正。
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

    支持思维链分析、多示例引导、智能错误诊断。

    Usage:
        gen = LLMStrategyGenerator(config)
        result = gen.generate("当5日均线上穿20日均线时买入")
    """

    def __init__(self, config):
        self._config = config
        self._client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    def generate(self, description: str) -> dict:
        """将自然语言描述转换为策略代码。

        两阶段流程：
          1. 思维链分析 + 代码生成
          2. 最多 {max_retries} 次智能诊断 + 修正

        Returns:
            {"code": str, "desc": str}
        """
        raw = self._call_llm(USER_PROMPT_TEMPLATE.format(description=description))
        gen_desc = self._extract_desc(raw)
        code = self._extract_code(raw)

        for attempt in range(self._config.max_retries):
            error_msg = self._validate_code(code)
            if error_msg is None:
                err = self.test_generated_code(code)
                if err is None:
                    logger.info("策略代码生成成功并通过验证")
                    return {"code": code, "desc": gen_desc or description[:60]}
                error_msg = f"运行时错误: {err}"

            diagnosis = self._diagnose_error(error_msg, code)
            logger.warning(
                f"策略代码验证失败 (尝试 {attempt + 1}/{self._config.max_retries}): "
                f"{error_msg[:80]} → {diagnosis[:60]}"
            )

            correction_prompt = ERROR_CORRECTION_PROMPT.format(
                description=description,
                diagnosis=diagnosis,
                code=code,
            )
            raw = self._call_llm(correction_prompt)
            code = self._extract_code(raw)

        raise RuntimeError(
            f"策略代码生成失败，已超过最大重试次数 ({self._config.max_retries})。"
            f"建议：1) 简化策略描述 2) 拆分为多个简单策略 3) 使用手动编写模式"
        )

    @staticmethod
    def _extract_desc(text: str) -> str:
        """从 LLM 响应中提取策略简介。"""
        # 优先从 thinking 块中提取第一句
        think_match = re.search(r"<thinking>(.*?)</thinking>", text, re.DOTALL)
        if think_match:
            lines = think_match.group(1).strip().split("\n")
            if lines:
                first = lines[0].strip()
                if first and len(first) < 80:
                    return first
        # Fallback: 取第一行非空内容
        lines = text.strip().split("\n")
        for line in lines:
            line = line.strip()
            if line and not line.startswith(("<", "```", "def ", "import ")):
                return line[:60]
        return ""

    def test_generated_code(self, code: str) -> Optional[str]:
        """在随机数据上测试生成的代码是否可执行。

        Returns:
            错误信息字符串，成功返回 None。
        """
        import pandas as pd
        import numpy as np

        # 260 条数据，覆盖 MA250 策略
        dates = pd.date_range("2025-01-01", periods=260, freq="D")
        test_data = pd.DataFrame({
            "open":   np.random.randn(260).cumsum() + 10,
            "high":   np.random.randn(260).cumsum() + 11,
            "low":    np.random.randn(260).cumsum() + 9,
            "close":  np.random.randn(260).cumsum() + 10,
            "volume": np.abs(np.random.randn(260)) * 1e6 + 1e6,
        }, index=dates)

        test_context = {
            "positions": {},
            "cash": 100000.0,
            "signals": [],
            "holdings": {},
        }

        try:
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
            if "strength" not in result or "reason" not in result:
                return "返回值缺少 'strength' 或 'reason' 字段"
            return None
        except Exception as e:
            return f"{type(e).__name__}: {e}"

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
                timeout=90,
            )
            content = response.choices[0].message.content
            return content or ""
        except Exception as e:
            logger.error(f"DeepSeek API 调用失败: {e}")
            raise RuntimeError(f"LLM 调用失败: {e}") from e

    @staticmethod
    def _extract_code(text: str) -> str:
        """从 LLM 响应中提取 Python 代码。

        优先提取 <code>...</code> 块，其次 ```python``` 块。
        """
        text = text.strip()

        # 方式 1: <code>...</code>
        m = re.search(r"<code>\s*\n?(.*?)</code>", text, re.DOTALL)
        if m:
            return m.group(1).strip()

        # 方式 2: ```python ... ```
        m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            return m.group(1).strip()

        # 方式 3: 直接以 def strategy 开头
        def_pos = text.find("def strategy")
        if def_pos >= 0:
            return text[def_pos:].strip()

        return text

    @staticmethod
    def _diagnose_error(error_msg: str, code: str) -> str:
        """智能诊断错误原因，给出针对性修正建议。

        不只是回传错误信息，而是分析代码找到问题点。
        """
        lines = code.split("\n")

        if "The truth value of a Series is ambiguous" in error_msg:
            # 找到可能出问题的行
            suspects = []
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if any(kw in stripped for kw in ["if ", "and ", "or ", "not "]):
                    if any(rolling in stripped for rolling in ["rolling", "shift", "diff"]):
                        if ".iloc[" not in stripped:
                            suspects.append(f"  L{i}: {stripped[:80]}")
            if suspects:
                return (
                    f"Series 布尔判断错误。以下行可能用了 Series 而非标量，"
                    f"需要用 .iloc[-1] 或 bool() 转换:\n" + "\n".join(suspects))
            return "Series 布尔判断错误。检查所有 if/and/or/not 条件，确保操作数是标量（用 .iloc[-1] 取值）。"

        if "SyntaxError" in error_msg:
            return f"语法错误: {error_msg}\n请检查缩进、括号匹配、冒号遗漏。"

        if "NameError" in error_msg:
            var_match = re.search(r"name '(\w+)' is not defined", error_msg)
            if var_match:
                return f"变量 '{var_match.group(1)}' 未定义。请检查拼写，或确认是否遗漏了赋值。"
            return f"变量未定义: {error_msg}"

        if "AttributeError" in error_msg:
            return f"属性/方法不存在: {error_msg}\n检查变量类型是否正确，rolling 结果是否用了 .iloc[]。"

        if "KeyError" in error_msg:
            return f"字典键不存在: {error_msg}\n确认 data 列名是否为 'open'/'high'/'low'/'close'/'volume'。"

        if "ValueError" in error_msg and "ndim" in error_msg:
            return "DataFrame 维度错误。检查是否误取了多列，用 data['close'] 取单列。"

        if "数据" in error_msg and "足" in error_msg:
            return "运行时数据不足。请增大开头的 len(data) < N 检查中的 N 值。"

        # 通用诊断
        return f"错误: {error_msg}\n请逐行检查代码逻辑和变量引用。"

    @staticmethod
    def _validate_code(code: str) -> Optional[str]:
        """静态验证生成的代码（AST 级别）。

        检查语法、函数签名、禁止的操作。
        """
        import ast

        if not code or not code.strip():
            return "生成的代码为空"

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return f"语法错误: {e}"

        # 检查 strategy 函数
        func_defs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        strategy_func = None
        for func in func_defs:
            if func.name == "strategy":
                strategy_func = func
                break

        if strategy_func is None:
            return "代码中未定义 strategy 函数"

        args = strategy_func.args
        if len(args.args) != 2:
            return f"strategy 函数必须有恰好 2 个参数 (context, data)，当前 {len(args.args)} 个"

        # 禁止的操作
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
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                return "禁止使用 global/nonlocal"
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in forbidden_calls:
                    return f"禁止调用: {node.func.id}"
                if isinstance(node.func, ast.Attribute) and node.func.attr in forbidden_calls:
                    return f"禁止调用: {node.func.attr}"
            if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs:
                return f"禁止访问属性: {node.attr}"

        return None


def _safe_builtins() -> dict:
    """返回受限制的 builtins。"""
    import builtins
    safe = {
        "True": True, "False": False, "None": None,
        "abs": abs, "all": all, "any": any, "bool": bool,
        "dict": dict, "enumerate": enumerate, "float": float,
        "int": int, "len": len, "list": list, "max": max,
        "min": min, "range": range, "round": round, "set": set,
        "slice": slice, "sorted": sorted, "str": str, "sum": sum,
        "tuple": tuple, "type": type, "zip": zip,
        "isinstance": isinstance, "hasattr": hasattr, "getattr": getattr,
        "print": lambda *a, **k: None,
    }
    return safe
