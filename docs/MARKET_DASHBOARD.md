# 市场研究驾驶舱

驾驶舱沿用 NASDX 的 Streamlit/Windows 桌面运行链，不引入 React、Node 或新的
前端构建步骤。独立启动：

```powershell
streamlit run nasdx/market_dashboard.py
```

也可在现有 Streamlit 页面中挂载：

```python
from nasdx.market_dashboard import render_market_dashboard

render_market_dashboard()
```

## 数据面板

| 面板 | 数据来源 | 无数据行为 |
|---|---|---|
| A 股关键指数 | 现有腾讯行情适配器 | 显示 unavailable，不阻断其他面板 |
| 全球关键指数 | `nasdx.global_market`（R2） | 依赖未合并时标记 dependency_pending |
| 市场宽度、板块资金、成交额聚合 | `nasdx.daily_review`（R3） | 依赖未合并时标记 dependency_pending |
| 7×24 快讯 | 华尔街见闻公开快讯接口 | 4 秒超时，空结果不缓存 |
| 大宗商品概览 | `nasdx.commodity_100ppi`（N4） | 只显示涨跌家数与平均变动，不显示品种排名 |
| 美债收益率曲线 | `nasdx.overseas_sources`（N5） | 依赖未合并时标记 dependency_pending |
| 产业链全景 | 本地行业环节 taxonomy | 不预置证券代码或证券名称 |

各数据模块保持自身缓存策略；驾驶舱本地抓取项采用 5 分钟 TTL，空结果不缓存。
页面只展示市场级、指数级、板块级与产业链环节数据，不包含个股榜单、买卖建议、
目标价或价格预测。
