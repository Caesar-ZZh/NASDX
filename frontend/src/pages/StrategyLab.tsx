import { useMemo, useState } from "react";
import { AlertCircle, BarChart3, FlaskConical, Loader2, Play, RefreshCw } from "lucide-react";
import type { EChartsOption } from "echarts";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { EChart } from "@/components/ui/EChart";
import { Disclaimer } from "@/components/ui/Disclaimer";
import {
  api,
  ApiError,
  type Etf50QuantResult,
  type QuantBacktestResult,
  type QuantBacktestRequest,
} from "@/lib/api";
import { cn } from "@/lib/utils";


type StrategyKey = QuantBacktestRequest["strategies"][number];

const STRATEGIES: Array<{ key: StrategyKey; label: string; note: string }> = [
  { key: "momentum", label: "动量策略", note: "按过去 20 日相对涨幅排序" },
  { key: "mean_reversion", label: "均值回归", note: "按过去 5 日相对跌幅排序" },
  { key: "factor_rank", label: "因子排名", note: "按仓库既有多因子评分排序" },
];
const COLORS = ["#52d3ff", "#8b7cff", "#35d0a0"];

const isoDay = (date: Date) => date.toISOString().slice(0, 10);
const defaultEnd = isoDay(new Date());
const defaultStart = (() => {
  const day = new Date();
  day.setFullYear(day.getFullYear() - 1);
  return isoDay(day);
})();
const percent = (value: number | null) => value == null ? "—" : `${(value * 100).toFixed(2)}%`;
const number = (value: number | null) => value == null ? "—" : value.toFixed(2);
const errorText = (error: unknown) => {
  const message = error instanceof ApiError ? error.message : "数据获取失败";
  return message.includes("超时") ? "加载超时，请重试" : message;
};


export function StrategyLab() {
  const [universe, setUniverse] = useState("510300, 510500, 159915, 588000, 512100");
  const [strategies, setStrategies] = useState<StrategyKey[]>(["momentum", "mean_reversion"]);
  const [start, setStart] = useState(defaultStart);
  const [end, setEnd] = useState(defaultEnd);
  const [rebalance, setRebalance] = useState("W");
  const [topN, setTopN] = useState(2);
  const [backtest, setBacktest] = useState<QuantBacktestResult | null>(null);
  const [backtestLoading, setBacktestLoading] = useState(false);
  const [backtestError, setBacktestError] = useState<string | null>(null);
  const [etf, setEtf] = useState<Etf50QuantResult | null>(null);
  const [etfLoading, setEtfLoading] = useState(false);
  const [etfError, setEtfError] = useState<string | null>(null);

  const toggleStrategy = (key: StrategyKey) => {
    setStrategies((current) => current.includes(key)
      ? current.filter((item) => item !== key)
      : [...current, key]);
  };

  const runBacktest = async () => {
    setBacktestError(null);
    if (!strategies.length) {
      setBacktestError("请至少选择一种策略");
      return;
    }
    const codes = universe.split(/[\s,，;；]+/).map((item) => item.trim()).filter(Boolean);
    setBacktestLoading(true);
    try {
      setBacktest(await api.quantBacktest({
        universe: codes,
        strategies,
        start,
        end,
        initial_capital: 100_000,
        rebalance,
        top_n: topN,
      }));
    } catch (error) {
      setBacktestError(errorText(error));
    } finally {
      setBacktestLoading(false);
    }
  };

  const loadEtf50 = async () => {
    setEtfError(null);
    setEtfLoading(true);
    try {
      setEtf(await api.quantEtf50(252, 5, "W"));
    } catch (error) {
      setEtfError(errorText(error));
    } finally {
      setEtfLoading(false);
    }
  };

  const backtestOption = useMemo<EChartsOption>(() => {
    const dates = Array.from(new Set(
      (backtest?.strategies ?? []).flatMap((row) => row.equity_curve.map((point) => point.date)),
    )).sort();
    return {
      tooltip: { trigger: "axis" },
      legend: { data: backtest?.strategies.map((row) => row.label) ?? [], textStyle: { color: "#94a3b8" } },
      grid: { left: 56, right: 20, top: 44, bottom: 38 },
      xAxis: { type: "category", data: dates, axisLabel: { color: "#64748b", hideOverlap: true } },
      yAxis: { type: "value", scale: true, axisLabel: { color: "#64748b" }, splitLine: { lineStyle: { color: "rgba(148,163,184,.12)" } } },
      series: (backtest?.strategies ?? []).map((row, index) => {
        const values = new Map(row.equity_curve.map((point) => [point.date, point.equity]));
        return {
          name: row.label,
          type: "line",
          showSymbol: false,
          smooth: 0.2,
          data: dates.map((day) => values.get(day) ?? null),
          lineStyle: { color: COLORS[index % COLORS.length], width: 2 },
        };
      }),
    };
  }, [backtest]);

  const etfRows = useMemo(
    () => (etf?.results ?? []).filter((row) => row.has_data).slice(0, 15),
    [etf],
  );
  const etfOption = useMemo<EChartsOption>(() => ({
    tooltip: { trigger: "axis" },
    grid: { left: 90, right: 24, top: 16, bottom: 24 },
    xAxis: { type: "value", min: 0, max: 100, axisLabel: { color: "#64748b" }, splitLine: { lineStyle: { color: "rgba(148,163,184,.12)" } } },
    yAxis: { type: "category", inverse: true, data: etfRows.map((row) => row.name || row.code), axisLabel: { color: "#94a3b8", width: 76, overflow: "truncate" } },
    series: [{ type: "bar", data: etfRows.map((row) => row.quant_score), itemStyle: { color: "#52d3ff", borderRadius: [0, 4, 4, 0] } }],
  }), [etfRows]);

  return (
    <div>
      <PageHeader
        title="策略实验室"
        subtitle="复用本地量化引擎，对历史数据做客观计算与多策略对比"
      />

      <div className="mb-5 flex items-start gap-2 rounded-lg border border-primary/25 bg-primary/5 p-3 text-sm text-muted-foreground">
        <FlaskConical className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <span><b className="text-foreground">客观计算、非荐股。</b> 历史回测和因子评分不代表未来表现，不构成投资建议。</span>
      </div>

      <GlassCard className="mb-6">
        <div className="mb-4 flex items-center gap-2">
          <BarChart3 className="h-4 w-4 text-primary" />
          <h2 className="font-bold">策略回测对比</h2>
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <label className="text-sm text-muted-foreground">
            股票池 / ETF 池（1–12 个六位代码）
            <input value={universe} onChange={(event) => setUniverse(event.target.value)} className="mt-1 w-full rounded-lg border border-border bg-background/50 px-3 py-2 text-foreground outline-none focus:border-primary" />
          </label>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <label className="text-sm text-muted-foreground">开始日期<input type="date" value={start} onChange={(event) => setStart(event.target.value)} className="mt-1 w-full rounded-lg border border-border bg-background/50 px-2 py-2 text-foreground" /></label>
            <label className="text-sm text-muted-foreground">结束日期<input type="date" value={end} onChange={(event) => setEnd(event.target.value)} className="mt-1 w-full rounded-lg border border-border bg-background/50 px-2 py-2 text-foreground" /></label>
            <label className="text-sm text-muted-foreground">调仓频率<select value={rebalance} onChange={(event) => setRebalance(event.target.value)} className="mt-1 w-full rounded-lg border border-border bg-background/50 px-2 py-2 text-foreground"><option value="W">每周</option><option value="M">每月</option><option value="D">每日</option></select></label>
            <label className="text-sm text-muted-foreground">持仓数量<input type="number" min={1} max={10} value={topN} onChange={(event) => setTopN(Number(event.target.value))} className="mt-1 w-full rounded-lg border border-border bg-background/50 px-2 py-2 text-foreground" /></label>
          </div>
        </div>
        <div className="mt-4 grid gap-2 sm:grid-cols-3">
          {STRATEGIES.map((item) => (
            <button key={item.key} onClick={() => toggleStrategy(item.key)} className={cn("rounded-lg border p-3 text-left transition-colors", strategies.includes(item.key) ? "border-primary/50 bg-primary/10" : "border-border bg-muted/10 hover:border-primary/25")}>
              <div className="text-sm font-semibold">{item.label}</div>
              <div className="mt-1 text-xs text-muted-foreground">{item.note}</div>
            </button>
          ))}
        </div>
        <div className="mt-4 flex items-center gap-3">
          <button disabled={backtestLoading} onClick={runBacktest} className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50">
            {backtestLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            {backtestLoading ? "计算中…" : "运行回测"}
          </button>
          {backtestError && <span className="flex items-center gap-1.5 text-sm text-warning"><AlertCircle className="h-4 w-4" />{backtestError}<button onClick={runBacktest} className="ml-1 underline">重试</button></span>}
        </div>
      </GlassCard>

      {backtest && (
        <GlassCard className="mb-6">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-bold">净值曲线</h3>
            <span className="text-xs text-muted-foreground">行情覆盖 {backtest.coverage.available}/{backtest.coverage.requested}</span>
          </div>
          <EChart option={backtestOption} height={330} />
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground"><tr><th className="py-2">策略</th><th>总收益</th><th>年化</th><th>Sharpe</th><th>最大回撤</th><th>胜率</th><th>闭环交易</th></tr></thead>
              <tbody>{backtest.strategies.map((row) => <tr key={row.strategy} className="border-t border-border/50"><td className="py-2 font-medium">{row.label}</td><td>{percent(row.metrics.total_return)}</td><td>{percent(row.metrics.annual_return)}</td><td>{number(row.metrics.sharpe_ratio)}</td><td>{percent(row.metrics.max_drawdown)}</td><td>{percent(row.metrics.win_rate)}</td><td>{row.metrics.completed_trades}</td></tr>)}</tbody>
            </table>
          </div>
        </GlassCard>
      )}

      <GlassCard>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h2 className="font-bold">ETF/50 量化评分</h2><p className="mt-1 text-xs text-muted-foreground">基于历史因子与组合回测的横截面评分，只展示客观排序。</p></div>
          <button disabled={etfLoading} onClick={loadEtf50} className="flex items-center gap-2 rounded-lg border border-primary/40 px-3 py-2 text-sm text-primary disabled:opacity-50">
            {etfLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            {etfLoading ? "计算中…" : etf ? "重新计算" : "加载评分"}
          </button>
        </div>
        {etfError && <p className="mt-4 flex items-center gap-2 text-sm text-warning"><AlertCircle className="h-4 w-4" />{etfError}<button onClick={loadEtf50} className="underline">重试</button></p>}
        {etf && <div className="mt-4 grid gap-5 lg:grid-cols-[1.2fr_1fr]"><EChart option={etfOption} height={420} /><div className="max-h-[420px] overflow-auto"><table className="w-full text-sm"><thead className="sticky top-0 bg-card text-left text-xs text-muted-foreground"><tr><th className="py-2">排名</th><th>代码</th><th>名称</th><th className="text-right">评分</th></tr></thead><tbody>{etfRows.map((row, index) => <tr key={row.code} className="border-t border-border/50"><td className="py-2">{index + 1}</td><td className="font-mono">{row.code}</td><td>{row.name}</td><td className="text-right font-mono text-primary">{row.quant_score.toFixed(1)}</td></tr>)}</tbody></table></div></div>}
      </GlassCard>

      <Disclaimer />
    </div>
  );
}
