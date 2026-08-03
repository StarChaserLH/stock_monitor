"""
标的管理模块。

支持两种模式：
  - specific: 从配置文件读取固定的 ETF 分组列表
  - full_market: 动态扫描全市场 ETF 并维护标的池
"""

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from app.market.data import MarketData

logger = logging.getLogger(__name__)


class SymbolManager:
    """标的管理器。

    负责加载、分组、筛选和维护监测标的池。

    Usage:
        mgr = SymbolManager(config, market_data)
        symbols = mgr.get_active_symbols()       # -> ['510050', '159915', ...]
        grouped = mgr.get_grouped_symbols()      # -> {'宽基ETF': [...], '行业ETF': [...]}
    """

    def __init__(self, config, market_data: MarketData):
        """
        Args:
            config: SymbolsConfig 实例。
            market_data: MarketData 实例，用于全市场模式获取 ETF 列表。
        """
        self._config = config
        self._market = market_data
        self._pool: list[str] = []      # 当前活动标的池（纯代码）
        self._meta: dict[str, dict] = {}  # 代码 -> 元信息（名称、类型等）
        self._groups: dict[str, list[str]] = {}  # 分组名 -> 代码列表
        self._refresh_pool()

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def get_active_symbols(self) -> list[str]:
        """返回当前活动标的代码列表。"""
        blacklist = set(self._config.blacklist)
        return [s for s in self._pool if s not in blacklist]

    def get_grouped_symbols(self) -> dict[str, list[str]]:
        """返回分组结构。"""
        return dict(self._groups)

    def get_meta(self, symbol: str) -> dict:
        """获取单个标的的元信息。"""
        return self._meta.get(symbol, {"name": symbol, "type": "unknown"})

    def set_mode(self, mode: str) -> None:
        """切换标的筛选模式。

        Args:
            mode: 'specific' 或 'full_market'。
        """
        if mode not in ("specific", "full_market"):
            raise ValueError(f"无效模式: {mode}")
        self._config.mode = mode
        self._refresh_pool()

    def add_symbol(self, symbol: str, group: str | None = None) -> None:
        """手动添加标的到监测池，同步持久化到 config.yaml。"""
        if group is None:
            group = "ETF" if symbol.startswith(("5", "1", "58", "16")) else "股票"
        if symbol not in self._pool:
            self._pool.append(symbol)
        self._groups.setdefault(group, [])
        if symbol not in self._groups[group]:
            self._groups[group].append(symbol)
        self._save_config_groups()

    def remove_symbol(self, symbol: str) -> None:
        """从监测池移除标的，同步持久化到 config.yaml。"""
        self._pool = [s for s in self._pool if s != symbol]
        for g in self._groups.values():
            if symbol in g:
                g.remove(symbol)
        self._save_config_groups()

    def _save_config_groups(self) -> None:
        """将当前分组写回 config.yaml。"""
        import yaml
        config_path = Path("config.yaml")
        if not config_path.exists():
            return
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            # 只更新 symbols.groups，保留其他字段
            raw.setdefault("symbols", {})["groups"] = {
                g: list(codes) for g, codes in self._groups.items() if codes
            }
            config_path.write_text(
                yaml.dump(raw, allow_unicode=True, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
            logger.info(f"配置已持久化: {len(self._pool)} 个标的")
        except Exception as e:
            logger.error(f"保存配置失败: {e}")

    def refresh(self) -> None:
        """强制刷新标的池（全市场模式时重新扫描）。"""
        self._refresh_pool()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _refresh_pool(self) -> None:
        """根据当前模式刷新标的池。"""
        if self._config.mode == "specific":
            self._load_from_config()
        else:
            self._scan_full_market()
        logger.info(f"标的池已刷新，共 {len(self._pool)} 个标的，模式={self._config.mode}")

    def _load_from_config(self) -> None:
        """从配置文件加载标的池。"""
        self._pool = []
        self._groups = {}

        for group_name, codes in self._config.groups.items():
            self._groups[group_name] = list(codes)
            for code in codes:
                if code not in self._pool:
                    self._pool.append(code)
                self._meta[code] = self._meta.get(code, {
                    "name": code,
                    "group": group_name,
                })

    def _scan_full_market(self) -> None:
        """扫描全市场 ETF，自动构建标的池。"""
        try:
            df = self._market.get_all_etfs()
            if df.empty:
                logger.warning("全市场 ETF 扫描返回空，回退到配置模式")
                self._load_from_config()
                return

            # akshare ETF 列表的常见列名
            code_col = None
            name_col = None
            for col in df.columns:
                if "代码" in col or "code" in str(col).lower():
                    code_col = col
                if "名称" in col or "name" in str(col).lower():
                    name_col = col

            if code_col is None:
                logger.warning("无法解析ETF代码列，回退到配置模式")
                self._load_from_config()
                return

            self._pool = []
            self._groups = {"全市场ETF": []}

            for _, row in df.iterrows():
                code = str(row[code_col])
                name = str(row.get(name_col, code)) if name_col else code
                self._pool.append(code)
                self._groups["全市场ETF"].append(code)
                self._meta[code] = {"name": name, "group": "全市场ETF"}

        except Exception as e:
            logger.error(f"全市场扫描失败: {e}，回退到配置模式")
            self._load_from_config()

    def to_dataframe(self) -> pd.DataFrame:
        """将当前标的池输出为 DataFrame，方便展示。"""
        rows = []
        for sym in self.get_active_symbols():
            meta = self._meta.get(sym, {})
            rows.append({
                "symbol": sym,
                "name": meta.get("name", sym),
                "group": meta.get("group", ""),
            })
        return pd.DataFrame(rows)
