// 实时驾驶舱：盘中一屏看全市场。
// 模块：① 大盘指数 KPI + 涨跌家数环图  ② 板块热力（treemap）  ③ 自选股实时报价表。
// 数据全部走既有后端接口（/indices、/market/overview、/industry、/quote），零后端改动。
// 轮询复用 useMarketPulse（5s，交易时段 + 页面可见才跑）与 useLiveQuotes（3s，自选股）。

import { useMemo, useState } from "react";
import { RefreshCw, Star, LayoutGrid, Gauge } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { EChart } from "@/components/ui/EChart";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { useMarketPulse } from "@/hooks/useMarketPulse";
import { useLiveQuotes, isTradingHours } from "@/hooks/useLiveQuotes";
import { loadWatch } from "@/lib/watchlist";
import { useDarkMode } from "@/hooks/useDarkMode";
import { chartColors } from "@/lib/chartTheme";
import { cn } from "@/lib/utils";
import type { EChartsOption } from "echarts";

const pctClass = (p: number) =>
  p > 0 ? "text-danger" : p < 0 ? "text-success" : "text-muted-foreground";

function fmtTime(ts: number | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function Cockpit() {
  const { dark } = useDarkMode();
  const pulse = useMarketPulse(true);
  const [watch] = useState<string[]>(() => loadWatch());
  const live = useLiveQuotes(watch, true);

  const c = chartColors();
  const sentiment = pulse.overview?.sentiment;

  // ---- 涨跌家数环图 ----
  const breadthOption = useMemo<EChartsOption>(() => {
    const up = sentiment?.up ?? 0;
    const down = sentiment?.down ?? 0;
    const flat = sentiment?.flat ?? 0;
    const total = up + down + flat;
    return {
      tooltip: { trigger: "item", formatter: "{b}: {c} 家 ({d}%)" },
      legend: { bottom: 0, left: "center", textStyle: { color: c.muted }, itemWidth: 10, itemHeight: 10 },
      title: {
        text: `${total}`, subtext: "全市场",
        left: "center", top: "32%",
        textAlign: "center",
        textStyle: { color: c.foreground, fontSize: 22, fontWeight: "bold" },
        subtextStyle: { color: c.muted, fontSize: 11 },
      },
      series: [
        {
          type: "pie",
          radius: ["54%", "76%"],
          center: ["50%", "44%"],
          avoidLabelOverlap: true,
          label: { show: false },
          itemStyle: { borderColor: c.grid, borderWidth: 2 },
          data: [
            { name: "上涨", value: up, itemStyle: { color: c.up } },
            { name: "下跌", value: down, itemStyle: { color: c.down } },
            { name: "平盘", value: flat, itemStyle: { color: c.muted } },
          ],
        },
      ],
    };
  }, [sentiment, c, dark]);

  // ---- 板块热力（treemap，面积均一、按 change_pct 着色）----
  const sectorOption = useMemo<EChartsOption>(() => {
    const rows = (pulse.industry?.top ?? []).slice(0, 28);
    if (!rows.length) return {};
    const vals = rows.map((r) => r.change_pct);
    let min = Math.min(...vals);
    let max = Math.max(...vals);
    if (min === max) max = min + 1;
    return {
      tooltip: {
        formatter: (p: any) => {
          const v = p.data.value[1] as number;
          return `${p.name}<br/>${v > 0 ? "+" : ""}${v}%`;
        },
      },
      visualMap: {
        type: "continuous",
        min,
        max,
        dimension: 1,
        show: false,
        inRange: { color: [c.down, "hsl(48 70% 50%)", c.up] },
      },
      series: [
        {
          type: "treemap",
          visualDimension: 1,
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          width: "100%",
          height: "100%",
          itemStyle: { borderColor: c.grid, borderWidth: 2, gapWidth: 2 },
          label: {
            show: true,
            color: c.foreground,
            fontSize: 11,
            formatter: (p: any) => {
              const v = p.data.value[1] as number;
              return `${p.name}\n${v > 0 ? "+" : ""}${v}%`;
            },
          },
          data: rows.map((r) => ({ name: r.name, value: [1, r.change_pct] })),
        },
      ],
    };
  }, [pulse.industry, c, dark]);

  const refreshAll = () => {
    pulse.refresh();
    live.refresh();
  };

  const liveState = pulse.polling ? "实时刷新中" : isTradingHours() ? "已暂停" : "已收盘";
  const topSector = pulse.industry?.top?.[0];
  const bottomSector = pulse.industry?.bottom?.[0];

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
              className="flex items-center gap-1 rounded-lg border border-border/60 px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground"
              title="立即刷新"
            >
              <RefreshCw className="h-3.5 w-3.5" /> 刷新
            </button>
          </div>
        }
      />

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
          <div className="col-span-full text-sm text-muted-foreground">大盘指数加载中…</div>
        )}
      </div>

      {/* ② 涨跌家数 + 板块热力 */}
      <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <GlassCard className="lg:col-span-1">
          <div className="mb-2 flex items-center gap-2">
            <Gauge className="h-4 w-4 text-primary" />
            <h3 className="text-sm font-bold">涨跌家数</h3>
          </div>
          <EChart option={breadthOption} height={236} />
          <div className="mt-1 grid grid-cols-2 gap-2 text-center text-xs">
            <div className="rounded-lg bg-danger/10 py-1.5">
              <div className="font-mono text-base font-bold text-danger">{(sentiment?.zt ?? 0)}</div>
              <div className="text-muted-foreground">涨停</div>
            </div>
            <div className="rounded-lg bg-success/10 py-1.5">
              <div className="font-mono text-base font-bold text-success">{(sentiment?.dt ?? 0)}</div>
              <div className="text-muted-foreground">跌停</div>
            </div>
          </div>
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
          <EChart option={sectorOption} height={300} />
          <p className="mt-1 text-center text-xs text-muted-foreground/60">
            共 {pulse.industry?.total ?? 0} 个板块 · 颜色越红越强、越绿越弱
          </p>
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
