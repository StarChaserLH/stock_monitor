<div align="center">

# StockMonitor — A股行情监测系统
<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/tests-161%20passed-brightgreen" alt="Tests">
  <img src="https://img.shields.io/badge/status-Active-success" alt="Status">
</p>

面向个人投资者的轻量级行情监测系统，实时盯盘 × AI多策略信号触发 × 回测闭环 × LOF 套利预警 × 邮件推送，量化监测一站搞定。
</div>

## 功能特性

- [x] **实时行情监测**：Sina API 获取 A 股 ETF / LOF / 个股行情，SQLite 本地缓存，8 线程并行
- [x] **8 个内置策略**：放量突破、缩量回踩、RSI 超卖反弹、ETF 轮动、神奇九转、布林带收缩、双均线金叉、ATR 移动止盈
- [x] **LLM 策略生成**：自然语言描述 → DeepSeek / OpenAI / 通义千问等自动生成 Python 策略代码 → AST 安全校验 → 一键启用
- [x] **回测引擎**：单策略 / 组合 / 多分组对比，年化收益、最大回撤、夏普比率、胜率、盈亏比
- [x] **大盘择时**：510300 基准，MA200 下方禁止买入，MA60 下方信号打折
- [x] **LOF 套利监测**：溢价率计算 + 成交额 + 申购状态 + 限额，可套利一键筛选
- [x] **邮件推送**：实时交易信号、LOF 日报（14:30）、收盘小结（15:01），免打扰 + 频率控制
- [x] **信号降噪**：同标的同日同方向去重，强度阈值过滤，过滤信号单独记录
- [x] **持仓管理**：手动记录真实买卖，卖出信号自动匹配持仓
- [x] **Web 管理台**：Flask + Jinja2 + Alpine.js + Tailwind CSS，12 个功能页面
- [x] **数据清理**：日志 1MB × 5 轮转，信号 90 天自动清理
- [x] **交易时段智能调度**：9:30-15:00 每 10 分钟监测，非交易时段自动休眠
## 快速开始

### 环境要求

- Python 3.10+，pip

### 安装与配置

```bash
git clone https://github.com/StarChaserLH/stock_monitor
cd stock-monitor/stock_monitor
pip install -r requirements.txt
cp config.yaml.example config.yaml  # 编辑自选股和邮箱
```

**配置邮箱推送**（`config.yaml`）：

```yaml
notification:
  enabled: true
  channels:
    email:
      enabled: true
      smtp_host: smtp.163.com
      smtp_port: 465
      username: your-email@163.com
      password: your-smtp-auth-code
      recipients:
        - your-email@qq.com
```

**配置自选股**（`config.yaml`）：

```yaml
symbols:
  mode: specific
  groups:
    ETF: [588000, 159915, 513050]
    个股: [688825, 601398, 601939]
```

**（可选）LLM 策略生成**（`.env`）：

```env
LLM_API_KEY=your-api-key-here  # DeepSeek / OpenAI / 通义千问等
```

### 运行

```bash
python run.py              # Web + 监测同时启动
python run.py web-only     # 仅 Web 管理台
python run.py monitor-only # 仅后台监测
python run.py once         # 单次执行，打印信号报告
python run.py account      # 查看持仓盈亏
```

访问 `http://localhost:5000`，默认密码 `admin123`（务必修改 `web.password`）。

生成邮件预览：

```bash
python tests/test_previews.py  # 输出到 templates/email/
```
## 系统架构

```
┌──────────────┐    ┌──────────────┐    ┌───────────────┐
│  Flask Web   │    │ MonitorLoop  │    │BacktestEngine │
│  管理台(11页) │    │  主调度循环   │    │  回测引擎      │
└──────┬───────┘    └──────┬───────┘    └──────┬────────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           │
       ┌───────────────────┼───────────────────┐
  ┌────▼────┐      ┌───────▼──────┐     ┌─────▼─────┐
  │ Market  │      │  Strategy    │     │  Notify   │
  │ 行情采集 │      │  策略沙箱    │     │  消息推送  │
  │ (Sina)  │      │  (exec 沙箱) │     │  (SMTP)   │
  └─────────┘      └──────────────┘     └───────────┘
```

**主循环数据流**：APScheduler 10 秒心跳 → 交易时段判断 → Sina 实时行情 + K线缓存(SQLite) → 8 策略 × N 标的 → 强度过滤 + 去重 + 大盘择时 → 信号入库 + 邮件推送

## 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.10+ |
| Web | Flask + Jinja2 + Alpine.js + Tailwind CSS |
| 图表 | Chart.js |
| 调度 | APScheduler |
| 数据 | pandas, numpy, SQLite |
| 行情 | akshare + Sina API |
| LLM | OpenAI SDK（DeepSeek / 通义千问 / 智谱 / Ollama 等） |
| 配置 | PyYAML + python-dotenv + pydantic |
| 推送 | SMTP（SSL/TLS 自适应） |
| 测试 | pytest（161 项） |

## 目录结构

```
stock_monitor/
├── run.py                           # 入口：Web / 监测 / 回测 / CLI
├── config.yaml.example              # 配置模板
├── .env.example                     # 环境变量模板
├── requirements.txt                 # Python 依赖
├── config.yaml                      # 用户配置（不提交）
├── data/                            # 运行时数据（不提交）
│   ├── kline.db                     # K线缓存
│   ├── strategies/strategies.db     # 策略 + 信号 + 回看
│   ├── positions.json              # 真实持仓
│   └── symbol_groups.json          # 标的分组
├── app/
│   ├── config.py                    # 配置加载（YAML + .env + pydantic）
│   ├── market/
│   │   ├── data.py                  # MarketData：实时行情 + K线（Sina）
│   │   ├── lof_data.py              # LOFDataProvider：溢价 + 申购 + 限额
│   │   └── lof_report.py            # LOF 日报 HTML 生成
│   ├── symbols/
│   │   ├── manager.py               # SymbolManager：标的池管理
│   │   └── groups.py                # GroupStore：分组持久化
│   ├── strategy/
│   │   ├── engine.py                # StrategyEngine：CRUD + 执行 + 信号回看 + 统计
│   │   ├── llm.py                   # LLMStrategyGenerator：自然语言生成策略
│   │   └── prompts.py               # LLM 提示词模板
│   ├── scheduler/
│   │   ├── loop.py                  # MonitorLoop：主调度 + 收盘小结 + LOF 报告
│   │   └── summary.py               # 收盘小结 HTML 生成器
│   ├── trade/
│   │   ├── broker.py                # 交易抽象基类
│   │   ├── paper.py                 # PaperBroker：模拟账户
│   │   └── positions.py             # PositionStore：真实持仓 JSON 存储
│   ├── notify/
│   │   ├── base.py                  # BaseNotifier：抽象基类 + 频率控制
│   │   ├── manager.py               # NotificationManager：多渠道统一管理
│   │   ├── email_.py                # EmailNotifier：SMTP SSL/TLS
│   │   └── wecom.py                 # WeComNotifier：企业微信机器人
│   ├── backtest/
│   │   ├── engine.py                # BacktestEngine：逐日回测 + 组合 + 对比
│   │   └── metrics.py               # 绩效指标（夏普/回撤/胜率/盈亏比）
│   └── web/
│       ├── server.py                # Flask 路由 + 30+ REST API
│       └── templates/               # Jinja2 页面（12 个）
├── templates/email/                 # 邮件 HTML 预览
├── tests/                           # pytest 测试（161 项）
│   ├── test_previews.py             # 邮件预览生成
│   ├── test_backtest.py             # 回测引擎测试
│   ├── test_positions.py            # 持仓存储测试
│   ├── test_groups.py               # 分组存储测试
│   ├── test_signal_review.py        # 信号回看测试
│   └── test_readme_api.py           # README API 一致性测试
└── logs/                            # 系统日志 + 推送历史
```


## 配置详解

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `scheduler.interval_seconds` | 600 | 交易时段监测间隔（10 分钟） |
| `scheduler.min_signal_strength` | 0.3 | 信号强度最低阈值 |
| `market_timing.enabled` | true | 大盘择时 MA200/MA60 过滤 |
| `market_timing.benchmark` | 510300 | 择时基准指数 |
| `market_timing.ma_bear` | 200 | MA200 下方禁止买入 |
| `market_timing.ma_weak` | 60 | MA60 下方信号打折（×0.7） |
| `lof.enabled` | true | LOF 溢价监测开关 |
| `lof.premium_threshold` | 5.0 | 溢价预警阈值（%） |
| `trading.initial_capital` | 100000 | 模拟账户初始资金 |
| `notification.quiet_hours` | 23-7 | 免打扰时段 |
| `notification.frequency.min_interval_minutes` | 10 | 同标题最小推送间隔 |
| `notification.frequency.max_per_hour` | 0 | 每小时最大推送数（0=不限） |
| `web.host` | 0.0.0.0 | Web 监听地址 |
| `web.port` | 5000 | Web 监听端口 |
| `web.password` | admin123 | **务必修改** |

## Web 管理台

| 页面 | 路由 | 功能 |
|------|------|------|
| 仪表盘 | `/dashboard` | 总资产+盈亏、LOF 溢价 Top 3、最新信号（仅持仓） |
| 持仓管理 | `/positions` | 手动记录真实买卖，卖出信号自动匹配 |
| 信号历史 | `/signals` | 今日全部信号，按标的分组，持仓/自选分类 |
| LOF 监测 | `/lof/premium` | 全市场溢价榜，可套利一键筛选 |
| 标的管理 | `/instruments` | 搜索/添加/移除自选股 |
| 策略管理 | `/strategies` | LLM 生成策略，查看/编辑/启用/禁用 |
| 策略回测 | `/backtest` | 单策略/组合/对比，Chart.js 权益曲线 |
| 推送配置 | `/push/settings` | 渠道开关，频率/免打扰设置 |
| 推送历史 | `/push/history` | 推送记录浏览 |
| 系统状态 | `/system` | 调度控制，日志查看 |

## 策略开发指南

**函数签名**：

```python
def strategy(context: dict, data: pd.DataFrame) -> dict:
    return {"action": "buy", "reason": "均线金叉", "strength": 0.8}
```

**返回值**：`action` 取 `"buy"` / `"sell"` / `"hold"`，`reason` 不超过 100 字，`strength` 范围 [0.0, 1.0]。

**允许使用**：`pd` (pandas)、`np` (numpy)。

**沙箱限制**：禁止 `import`、`eval`、`exec`、`open`、`__import__`、`getattr`、网络请求、文件读写。代码经 AST 静态扫描 + 运行时 `_safe_builtins()` 双重保护。

**添加策略**：将 `.py` 文件放入 `data/strategies/`，通过 Web 管理台 或 `StrategyEngine.create()` 注册。

## 注意事项

- 策略代码在受限沙箱中运行，危险操作会被 AST 扫描拦截
- 数据源为免费 Sina API，非交易时段系统自动休眠
- 系统不绑定真实券商，请在 Web 持仓管理手动记录买卖
- `config.yaml` 含邮箱授权码等敏感信息，已加入 `.gitignore` 不提交
- 日志 1MB × 5 自动轮转，信号 90 天自动清理
- LOF 净值数据每天 22:00 后刷新

## 推送时间表

| 时间 | 邮件标题 | 内容 |
|------|---------|------|
| 交易时段每 10 分钟 | `[自选] 交易信号` / `交易信号` | 实时买入/卖出触发 |
| 14:30 | `LOF 日报` | 全市场溢价榜 + 可套利预警 |
| 15:01 | `收盘小结` | 持仓全景 + 信号回顾 + 需关注 + 接近触发 |

## 许可证

MIT License — 详见 [LICENSE](LICENSE)
