// 实时驾驶舱：盘中一屏看全市场。
// 模块：① 大盘指数 KPI + Lieflat Tick Donut  ② 板块双榜热力矩阵  ③ 自选股实时报价表。
// 数据走既有后端接口（/indices、/market/overview、/industry、/quote）。
// 轮询复用 useMarketPulse（5s，交易时段 + 页面可见才跑）与 useLiveQuotes（3s，自选股）。

import { useState } from "react";
import { AlertCircle, RefreshCw, Star, LayoutGrid, Gauge } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { MarketBreadthField } from "@/components/charts/MarketBreadthField";
import { SectorHeatBoard } from "@/components/charts/SectorHeatBoard";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { useMarketPulse } from "@/hooks/useMarketPulse";
import { useLiveQuotes, isTradingHours } from "@/hooks/useLiveQuotes";
import { loadWatch } from "@/lib/watchlist";
import { cn } from "@/lib/utils";

const pctClass = (p: number) =>
  p > 0 ? "text-danger" : p < 0 ? "text-success" : "text-muted-foreground";

function fmtTime(ts: number | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function Cockpit() {
  const pulse = useMarketPulse(true);
  const [watch] = useState<string[]>(() => loadWatch());
  const live = useLiveQuotes(watch, true);

  const sentiment = pulse.overview?.sentiment;

  const refreshAll = () => {
    pulse.refresh();
    live.refresh();
  };

  const liveState = pulse.polling ? "实时刷新中" : isTradingHours() ? "已暂停" : "已收盘";
  const topSector = pulse.industry?.top?.[0];
  const bottomRows = pulse.industry?.bottom ?? [];
  const bottomSector = bottomRows[bottomRows.length - 1];

  return (
    <div>
      <PageHeader
        title="实时驾驶舱"
        subtitle="大盘 · 涨跌家数 · 板块热力 · 自选股 —— 盘中一屏看全市场"
        actions={
          <div className="flex items-center gap-2">
            <span className={cn("h-2 w-2 rounded-full", pulse.polling ? "bg-success animate-pulse" : "bg-muted-foreground/50")} />
            <span className="text-xs text-muted-foreground">{liveState}</span>
            <span className="text-xs text-muted-foreground/70">更新 {fmtTime(pulse.updatedAt ?? live.updatedAt)}</span>
            <button
              onClick={refreshAll}
              className="flex items-center gap-1 rounded-lg border border-border/60 px-2.5 py-2 text-xs text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground md:py-1"
              title="立即刷新"
            >
              <RefreshCw className="h-3.5 w-3.5" /> 刷新
            </button>
          </div>
        }
      />

      {pulse.error && (
        <div className="mb-4 flex items-center justify-between rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning">
          <span className="flex items-center gap-2"><AlertCircle className="h-4 w-4" />{pulse.error}</span>
          <button onClick={pulse.refresh} className="text-xs font-medium underline underline-offset-2">重试</button>
        </div>
      )}

      {/* ① 大盘指数 KPI */}
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
        {(pulse.indices ?? []).map((i) => (
          <GlassCard key={i.name} className="flex flex-col">
            <p className="truncate text-xs text-muted-foreground">{i.name}</p>
            <p className="mt-1 font-mono text-xl font-bold">{i.price}</p>
            <p className={cn("mt-0.5 text-sm font-medium", pctClass(i.change_pct))}>
              {i.change_pct > 0 ? "+" : ""}
              {i.change_pct}%
            </p>
          </GlassCard>
        ))}
        {!pulse.indices && (
          <div className="col-span-full text-sm text-muted-foreground">
            {pulse.loading ? "大盘指数加载中…" : pulse.error ? "大盘指数加载超时，可点上方重试" : "大盘指数暂无数据"}
          </div>
        )}
      </div>

      {/* ② 涨跌家数 + 板块热力 */}
      <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <GlassCard className="lg:col-span-1">
          <div className="mb-2 flex items-center gap-2">
            <Gauge className="h-4 w-4 text-primary" />
            <h3 className="text-sm font-bold">涨跌家数</h3>
          </div>
          {sentiment ? <MarketBreadthField sentiment={sentiment} /> : (
            <p className="flex h-[236px] items-center justify-center text-sm text-muted-foreground">
              {pulse.loading ? "涨跌家数加载中…" : pulse.error ? "涨跌家数加载超时，可点上方重试" : "涨跌家数暂无数据"}
            </p>
          )}
          {sentiment && <div className="mt-1 grid grid-cols-2 gap-2 text-center text-xs">
            <div className="rounded-lg bg-danger/10 py-1.5">
              <div className="font-mono text-base font-bold text-danger">{(sentiment?.zt ?? 0)}</div>
              <div className="text-muted-foreground">涨停</div>
            </div>
            <div className="rounded-lg bg-success/10 py-1.5">
              <div className="font-mono text-base font-bold text-success">{(sentiment?.dt ?? 0)}</div>
              <div className="text-muted-foreground">跌停</div>
            </div>
          </div>}
        </GlassCard>

        <GlassCard className="lg:col-span-2">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <LayoutGrid className="h-4 w-4 text-primary" />
              <h3 className="text-sm font-bold">板块热力</h3>
            </div>
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              {topSector && (
                <span>
                  领涨 <span className="text-danger">{topSector.name} {topSector.change_pct > 0 ? "+" : ""}{topSector.change_pct}%</span>
                </span>
              )}
              {bottomSector && (
                <span>
                  领跌 <span className="text-success">{bottomSector.name} {bottomSector.change_pct > 0 ? "+" : ""}{bottomSector.change_pct}%</span>
                </span>
              )}
            </div>
          </div>
          {(pulse.industry?.top?.length ?? 0) > 0 && (pulse.industry?.bottom?.length ?? 0) > 0 ? (
            <SectorHeatBoard industry={pulse.industry!} />
          ) : (
            <p className="flex h-[300px] items-center justify-center text-sm text-muted-foreground">
              {pulse.loading ? "板块热力加载中…" : pulse.error ? "板块热力加载超时，可点上方重试" : "板块热力暂无数据"}
            </p>
          )}
        </GlassCard>
      </div>

      {/* ③ 自选股实时报价 */}
      <GlassCard>
        <div className="mb-3 flex items-center gap-2">
          <Star className="h-4 w-4 text-primary" />
          <h3 className="text-sm font-bold">自选股实时报价</h3>
          {live.polling && <span className="text-xs text-muted-foreground/70">· 3s 轮询</span>}
        </div>

        {watch.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            还没添加自选股 —— 去「自选股」页粘贴代码即可在此实时盯盘。
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/50 text-xs text-muted-foreground">
                  <th className="px-2 py-2 text-left font-medium">名称 / 代码</th>
                  <th className="px-2 py-2 text-right font-medium">最新价</th>
                  <th className="px-2 py-2 text-right font-medium">涨跌幅</th>
                  <th className="px-2 py-2 text-right font-medium">换手率</th>
                  <th className="px-2 py-2 text-right font-medium">总市值(亿)</th>
                  <th className="px-2 py-2 text-right font-medium">市盈率(TTM)</th>
                </tr>
              </thead>
              <tbody>
                {watch.map((code) => {
                  const q = live.quotes[code];
                  return (
                    <tr key={code} className="border-b border-border/30 last:border-0">
                      <td className="px-2 py-2">
                        <div className="font-medium">{q?.name ?? code}</div>
                        <div className="font-mono text-xs text-muted-foreground">{code}</div>
                      </td>
                      <td className="px-2 py-2 text-right font-mono">
                        {q ? q.price.toFixed(2) : "—"}
                      </td>
                      <td className={cn("px-2 py-2 text-right font-mono font-medium", q ? pctClass(q.change_pct) : "text-muted-foreground/40")}>
                        {q ? `${q.change_pct > 0 ? "+" : ""}${q.change_pct}%` : "—"}
                      </td>
                      <td className="px-2 py-2 text-right font-mono text-muted-foreground">
                        {q ? `${q.turnover_pct.toFixed(2)}%` : "—"}
                      </td>
                      <td className="px-2 py-2 text-right font-mono text-muted-foreground">
                        {q ? q.mcap_yi.toFixed(0) : "—"}
                      </td>
                      <td className="px-2 py-2 text-right font-mono text-muted-foreground">
                        {q && q.pe_ttm ? q.pe_ttm.toFixed(1) : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </GlassCard>

      <Disclaimer />
    </div>
  );
}
