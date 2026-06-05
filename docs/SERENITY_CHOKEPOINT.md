# Serenity Chokepoint 集成说明

## 用途

把 Serenity Chokepoint Investing 的研究框架接入 NASDX，作为 `chokepoint` 研究维度：

- 需求冲击：AI数据中心、半导体国产化、CPO/光模块、电网升级、军工国产化等。
- 供应链拆解：从终端需求向上拆到组件、材料、设备、产能或资质节点。
- 卡点判断：稀缺性、扩产难度、认证周期、替代难度、架构绑定。
- 财务映射：判断主题是否能转化为收入、毛利率、订单、估值重估或仅是叙事。
- 贝叶斯更新：把新公告、财报、客户验证、补贴、负面报告等作为正反证据更新假设。

## 当前边界

NASDX 当前只把股票池备注、板块、技术指标、资金流数据传给该 Agent；未自动抓取公告、财报、互动平台或新闻。涉及客户、订单、产能、市场份额、政策文件的内容必须标为待核验。

## 来源

参考 GitHub 仓库：`xvhaoran778-cyber/Serenity.SKILL`，路径 `skills/serenity-chokepoint-investing`。
