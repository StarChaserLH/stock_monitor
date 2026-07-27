"""
标的分组持久化存储。

格式: {"name": {"symbols": [...], "note": "..."}}
默认分组（全部 / ETF / 个股）为计算型，不存储。
"""

import json
import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

DEFAULT_GROUPS: dict[str, Callable[[list[str]], list[str]]] = {
    "全部": lambda syms: list(syms),
    "ETF": lambda syms: [s for s in syms if s.startswith(("5", "1", "58", "16"))],
    "个股": lambda syms: [s for s in syms if not s.startswith(("5", "1", "58", "16"))],
}


class GroupStore:
    """自定义分组持久化存储。"""

    def __init__(self, filepath: str = "data/symbol_groups.json"):
        self._path = Path(filepath)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._groups: dict[str, dict] = {}  # name -> {symbols, note}
        self._load()

    # ------------------------------------------------------------------
    # 公开接口

    def list_names(self) -> list[str]:
        return list(self._groups.keys())

    def list_all(self) -> dict[str, dict]:
        return dict(self._groups)

    def get(self, name: str) -> dict | None:
        return self._groups.get(name)

    def get_symbols(self, name: str) -> list[str]:
        g = self._groups.get(name)
        return list(g["symbols"]) if g else []

    def get_note(self, name: str) -> str:
        g = self._groups.get(name)
        return g.get("note", "") if g else ""

    def save(self, name: str, symbols: list[str], note: str = "") -> None:
        if name in DEFAULT_GROUPS:
            raise ValueError(f"'{name}' 是默认分组，不可覆盖")
        self._groups[name] = {"symbols": list(symbols), "note": note}
        self._flush()
        logger.info(f"分组已保存: {name} ({len(symbols)} 只)")

    def delete(self, name: str) -> bool:
        if name not in self._groups:
            return False
        del self._groups[name]
        self._flush()
        logger.info(f"分组已删除: {name}")
        return True

    def ensure_defaults(self) -> None:
        if not self._groups:
            self.save("示例", ["510050", "159915"], "回测表现最好的宽基")

    # ------------------------------------------------------------------
    # 内部

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if isinstance(v, list):
                        # 兼容旧格式: {name: [symbols]}
                        self._groups[k] = {"symbols": v, "note": ""}
                    elif isinstance(v, dict):
                        self._groups[k] = {
                            "symbols": list(v.get("symbols", [])),
                            "note": v.get("note", ""),
                        }
            except (json.JSONDecodeError, KeyError):
                logger.warning("分组文件损坏，从空开始")

    def _flush(self) -> None:
        self._path.write_text(
            json.dumps(self._groups, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
