"""
NASDX × 同花顺 接入桥
功能：
  1. 读取持仓 / 自选股
  2. 自动买入 / 卖出（需同花顺客户端在前台运行）
  3. 实时行情（pytdx 通达信协议，免费）

依赖：
  pip install easytrader pytdx
前置条件：
  同花顺客户端已登录，并置于任务栏（不需要在前台，但不能最小化到托盘后退出）
"""

from __future__ import annotations
import time
import json
from pathlib import Path
from typing import Any

# ══════════════════════════════════════════
#  1. 同花顺交易接口（easytrader）
# ══════════════════════════════════════════
class THSTrader:
    """
    封装 easytrader 的同花顺交易接口
    使用前：同花顺客户端必须已登录并运行
    """

    def __init__(self):
        self._client = None

    def connect(self) -> bool:
        """连接同花顺客户端"""
        try:
            import easytrader
            self._client = easytrader.use("ths")  # ths = 同花顺
            print("✅ 同花顺客户端连接成功")
            return True
        except Exception as e:
            print(f"❌ 连接失败：{e}")
            print("   请确认：同花顺客户端已登录并在任务栏运行")
            return False

    def get_balance(self) -> dict:
        """获取账户余额"""
        if not self._client:
            return {}
        try:
            return self._client.balance
        except Exception as e:
            print(f"获取余额失败：{e}")
            return {}

    def get_position(self) -> list[dict]:
        """
        获取当前持仓
        返回: [{'证券代码': '600519', '证券名称': '贵州茅台', '持仓量': 100, '可用量': 100,
                '成本价': 1200.0, '市价': 1280.0, '盈亏': 8000.0, ...}]
        """
        if not self._client:
            return []
        try:
            pos = self._client.position
            if isinstance(pos, list):
                return pos
            return []
        except Exception as e:
            print(f"获取持仓失败：{e}")
            return []

    def get_today_orders(self) -> list[dict]:
        """获取今日委托"""
        if not self._client:
            return []
        try:
            return self._client.today_orders or []
        except Exception as e:
            print(f"获取委托失败：{e}")
            return []

    def buy(self, code: str, price: float, amount: int) -> dict:
        """
        买入
        code: 股票代码（不含市场前缀）
        price: 买入价格（传 0 则市价）
        amount: 买入数量（股，ETF最小100股）
        """
        if not self._client:
            return {"success": False, "msg": "未连接"}
        try:
            result = self._client.buy(code, price=price, amount=amount)
            print(f"📈 买入 {code} {amount}股 @{price:.3f} → {result}")
            return {"success": True, "result": result}
        except Exception as e:
            print(f"❌ 买入失败：{e}")
            return {"success": False, "msg": str(e)}

    def sell(self, code: str, price: float, amount: int) -> dict:
        """
        卖出
        price: 传 0 则市价卖出
        """
        if not self._client:
            return {"success": False, "msg": "未连接"}
        try:
            result = self._client.sell(code, price=price, amount=amount)
            print(f"📉 卖出 {code} {amount}股 @{price:.3f} → {result}")
            return {"success": True, "result": result}
        except Exception as e:
            print(f"❌ 卖出失败：{e}")
            return {"success": False, "msg": str(e)}

    def cancel_all(self):
        """撤销全部未成交委托"""
        if not self._client:
            return
        try:
            self._client.cancel_entrust("all")
            print("已撤销全部委托")
        except Exception as e:
            print(f"撤销失败：{e}")

    def smart_buy(self, code: str, total_amount: float, signal_score: int) -> dict:
        """
        智能买入：根据 NASDX 评分决定仓位比例
        total_amount: 可用资金（元）
        signal_score: NASDX 技术面评分（0-100）
        """
        # 评分 → 仓位比例
        if signal_score >= 90:
            ratio = 0.40
        elif signal_score >= 80:
            ratio = 0.30
        elif signal_score >= 70:
            ratio = 0.20
        elif signal_score >= 65:
            ratio = 0.10
        else:
            return {"success": False, "msg": f"评分 {signal_score} 不足65分，不买入"}

        # 获取当前价格
        price = get_realtime_price(code)
        if not price:
            return {"success": False, "msg": "无法获取实时价格"}

        # 计算手数（100股为一手）
        buy_amount_yuan = total_amount * ratio
        lots = int(buy_amount_yuan / (price * 100))  # 手
        shares = lots * 100

        if shares < 100:
            return {"success": False, "msg": f"资金不足，最少需要 {price*100:.0f} 元"}

        print(f"智能买入：{code} 评分{signal_score}→仓位{ratio:.0%}→{shares}股 @{price:.3f} 共{shares*price:.0f}元")
        return self.buy(code, price=price, amount=shares)


# ══════════════════════════════════════════
#  2. 实时行情（pytdx 通达信）
# ══════════════════════════════════════════

_tdx_api = None

def _get_tdx():
    """获取行情 API（优先 mootdx，备用 pytdx）"""
    global _tdx_api
    if _tdx_api is not None:
        return _tdx_api
    # 优先用 mootdx（纯Python，无需编译）
    # bestip=False：不再逐服务器测速选优（实测 7.7s+ 且常 WinError 10054），
    # 固定走默认服务器快速连接，失败立即降级 pytdx。
    try:
        from mootdx.quotes import Quotes
        api = Quotes.factory(market="std", bestip=False, timeout=5)
        _tdx_api = ("mootdx", api)
        print("✅ 行情连接（mootdx）")
        return _tdx_api
    except Exception as e:
        print(f"mootdx 失败：{e}")
    # 备用 pytdx
    try:
        from pytdx.hq import TdxHq_API
        api = TdxHq_API()
        SERVERS = [("119.147.212.81",7709),("221.194.181.176",7709),("112.74.214.43",7709)]
        for host, port in SERVERS:
            try:
                if api.connect(host, port):
                    _tdx_api = ("pytdx", api)
                    print(f"✅ 行情连接（pytdx）：{host}:{port}")
                    return _tdx_api
            except Exception:
                continue
    except ImportError:
        pass
    print("❌ 请安装：pip install mootdx")
    return None


def _market_code(code: str) -> int:
    """判断市场：0=深圳 1=上海"""
    if code.startswith(("6", "5", "11")):
        return 1   # 上海
    return 0       # 深圳


def get_realtime_price(code: str) -> float | None:
    """获取单只股票/ETF 的实时价格"""
    result = get_realtime_batch([code])
    return result.get(code, {}).get("price")


def get_realtime_batch(codes: list[str]) -> dict[str, dict]:
    """批量获取实时行情，自动适配 mootdx / pytdx"""
    conn = _get_tdx()
    if not conn:
        return {}

    kind, api = conn
    results = {}

    if kind == "mootdx":
        # mootdx 接口
        try:
            import pandas as pd
            df = api.quotes(symbol=codes)
            if df is None or df.empty:
                return {}
            for _, row in df.iterrows():
                c = str(row.get("code","")).zfill(6)
                price = float(row.get("price", row.get("close", 0)) or 0)
                last  = float(row.get("last_close", row.get("yesterday_close", price)) or price)
                chg_pct = (price/last - 1)*100 if last else 0
                results[c] = {
                    "price":      price,
                    "change":     price - last,
                    "change_pct": round(chg_pct, 2),
                    "volume":     int(row.get("vol", row.get("volume", 0)) or 0),
                    "amount":     float(row.get("amount", row.get("turnover", 0)) or 0),
                    "high":       float(row.get("high", 0) or 0),
                    "low":        float(row.get("low", 0) or 0),
                    "open":       float(row.get("open", 0) or 0),
                    "bid1":       float(row.get("bid1", 0) or 0),
                    "ask1":       float(row.get("ask1", 0) or 0),
                }
        except Exception as e:
            print(f"mootdx 批量行情失败：{e}")

    else:
        # pytdx 接口
        for i in range(0, len(codes), 20):
            batch = codes[i:i+20]
            pairs = [(_market_code(c), c) for c in batch]
            try:
                data = api.get_security_quotes(pairs)
                if data:
                    for item in data:
                        c = item.get("code","")
                        if c:
                            p   = float(item.get("price", 0))
                            lc  = float(item.get("last_close", p) or p)
                            results[c] = {
                                "price":      p,
                                "change":     p - lc,
                                "change_pct": round((p/lc-1)*100 if lc else 0, 2),
                                "volume":     int(item.get("vol", 0)),
                                "amount":     float(item.get("amount", 0)),
                                "high":       float(item.get("high", 0)),
                                "low":        float(item.get("low", 0)),
                                "open":       float(item.get("open", 0)),
                                "bid1":       float(item.get("bid1", 0)),
                                "ask1":       float(item.get("ask1", 0)),
                            }
            except Exception as e:
                print(f"pytdx 批量行情失败：{e}")
            time.sleep(0.05)

    return results


def get_kline(code: str, days: int = 90) -> list[dict]:
    """
    获取日K线数据（替代 AkShare，速度更快）
    返回: [{'date': '2026-06-04', 'open': ..., 'close': ..., 'high': ..., 'low': ..., 'volume': ...}]
    """
    api = _get_tdx()
    if not api:
        return []
    try:
        market = _market_code(code)
        raw = api.get_security_bars(9, market, code, 0, days)  # 9=日K
        if not raw:
            return []
        result = []
        for bar in raw:
            result.append({
                "date":   bar.get("datetime", "")[:10],
                "open":   round(float(bar.get("open", 0)), 3),
                "close":  round(float(bar.get("close", 0)), 3),
                "high":   round(float(bar.get("high", 0)), 3),
                "low":    round(float(bar.get("low", 0)), 3),
                "volume": int(bar.get("vol", 0)),
                "amount": float(bar.get("amount", 0)),
            })
        return sorted(result, key=lambda x: x["date"])
    except Exception as e:
        print(f"获取 {code} K线失败：{e}")
        return []


# ══════════════════════════════════════════
#  3. 自选股同步
# ══════════════════════════════════════════
def sync_watchlist_from_ths(trader: THSTrader) -> list[str]:
    """
    从同花顺持仓同步代码列表（持仓股 + 可扩展为自选）
    返回股票代码列表
    """
    position = trader.get_position()
    codes = [p.get("证券代码", p.get("stock_code", "")) for p in position if p]
    codes = [c for c in codes if c]
    print(f"持仓股票：{codes}")
    return codes


# ══════════════════════════════════════════
#  4. NASDX 自动交易策略
# ══════════════════════════════════════════
class NasdxAutoTrader:
    """
    将 NASDX 分析结果自动执行到同花顺
    策略：
      - 评分 ≥ 80 且溢价 < 1%  → 自动买入
      - 持仓股评分 ≤ 40         → 自动卖出
      - 每日最多操作 3 只，单只不超过仓位 40%
    """

    def __init__(self, total_capital: float = 26000, max_single_ratio: float = 0.4):
        self.total_capital = total_capital
        self.max_single_ratio = max_single_ratio
        self.trader = THSTrader()
        self.connected = False

    def connect(self):
        self.connected = self.trader.connect()
        return self.connected

    def run_once(self, etf50_json_path: str, dry_run: bool = True):
        """
        执行一次自动交易
        dry_run=True: 只打印，不实际下单（安全模式）
        """
        if not self.connected and not dry_run:
            print("❌ 未连接同花顺，请先 connect()")
            return

        # 读取最新 ETF50 扫描结果
        with open(etf50_json_path, encoding="utf-8") as f:
            data = json.load(f)

        results = data.get("results", [])
        balance = self.trader.get_balance() if self.connected else {"可用金额": self.total_capital}
        available = float(balance.get("可用金额", self.total_capital))

        # 获取当前持仓
        position = self.trader.get_position() if self.connected else []
        holding_codes = {p.get("证券代码","") for p in position}

        print(f"\n{'='*55}")
        print(f"  NASDX 自动交易  {'[演练模式]' if dry_run else '[实盘模式⚠️]'}")
        print(f"  可用资金: {available:.0f} 元")
        print(f"  当前持仓: {holding_codes or '无'}")
        print(f"{'='*55}")

        buy_list = []
        sell_list = []

        for r in results:
            code = r.get("code", "")
            score = r.get("score", 0)
            signal = r.get("signal", "neutral")
            prem = r.get("premium")

            # 买入条件：评分≥80，溢价<1%，未持有
            if signal == "bullish" and score >= 80 and code not in holding_codes:
                if prem is None or prem < 1.0:
                    buy_list.append(r)

            # 卖出条件：已持有 + 看空
            if code in holding_codes and signal == "bearish":
                sell_list.append(r)

        # 最多买3只，按评分排序
        buy_list = sorted(buy_list, key=lambda x: -x["score"])[:3]

        print(f"\n📈 买入信号（{len(buy_list)}只）:")
        for r in buy_list:
            price = get_realtime_price(r["code"]) or r.get("spot_price", 0)
            budget = available * self.max_single_ratio / max(len(buy_list), 1)
            shares = int(budget / (price * 100)) * 100 if price else 0
            cost = shares * price if price else 0
            prem = r.get("premium")
            prem_s = f'溢价{prem:+.2f}%' if prem is not None else ""
            print(f"  {'[演练]' if dry_run else '[下单]'} {r['code']} {r['name']}")
            print(f"    评分:{r['score']}  价格:{price:.3f}  数量:{shares}股  金额:{cost:.0f}元  {prem_s}")
            if not dry_run and shares > 0:
                self.trader.buy(r["code"], price=price, amount=shares)

        print(f"\n📉 卖出信号（{len(sell_list)}只）:")
        for r in sell_list:
            pos_item = next((p for p in position if p.get("证券代码","") == r["code"]), {})
            shares = int(pos_item.get("可用量", 0))
            price = get_realtime_price(r["code"]) or r.get("spot_price", 0)
            print(f"  {'[演练]' if dry_run else '[下单]'} {r['code']} {r['name']}  {shares}股 @{price:.3f}")
            if not dry_run and shares > 0:
                self.trader.sell(r["code"], price=price, amount=shares)

        if not buy_list and not sell_list:
            print("  → 无符合条件的交易信号，今日观望")

        print(f"\n{'='*55}")


# ══════════════════════════════════════════
#  快速测试入口
# ══════════════════════════════════════════
if __name__ == "__main__":
    import glob, sys

    print("=== NASDX × 同花顺 接入测试 ===\n")

    # 1. 测试实时行情
    print("1. 测试通达信实时行情...")
    prices = get_realtime_batch(["512480","159611","513160","600519"])
    for code, info in prices.items():
        print(f"   {code}: {info['price']:.3f}  {info['change_pct']:+.2f}%")

    # 2. 测试同花顺连接（需客户端运行）
    print("\n2. 测试同花顺连接...")
    trader = THSTrader()
    if trader.connect():
        balance = trader.get_balance()
        print(f"   余额: {balance}")
        pos = trader.get_position()
        print(f"   持仓: {len(pos)} 只")
        for p in pos[:3]:
            print(f"   - {p}")

    # 3. 演练自动交易
    print("\n3. 演练自动交易...")
    files = sorted(glob.glob("reports/etf50_*.json"))
    if files:
        auto = NasdxAutoTrader(total_capital=26000)
        auto.connected = False  # 演练模式不需要连接
        auto.run_once(files[-1], dry_run=True)
    else:
        print("   无ETF扫描数据，请先运行 scan_etf50.py")
