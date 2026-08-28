import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import type { IndustryData, IndustryRow } from "@/lib/api";

interface Props {
  industry: IndustryData;
}

const PAGE_SIZE = 6;

function heatColor(value: number, bound: number): string {
  const strength = Math.min(Math.abs(value) / Math.max(bound, 0.01), 1);
  if (value > 0) return `hsl(0 68% ${30 + strength * 20}%)`;
  if (value < 0) return `hsl(153 55% ${27 + strength * 20}%)`;
  return "hsl(215 18% 32%)";
}

function HeatRow({ row, bound, index }: { row: IndustryRow; bound: number; index: number }) {
  const pct = row.change_pct;
  return (
    <div
      className="flex h-9 items-center gap-2 rounded-md border border-white/10 px-2.5 text-white shadow-sm transition-transform hover:-translate-y-0.5"
      style={{ backgroundColor: heatColor(pct, bound) }}
      title={`${row.name} ${pct > 0 ? "+" : ""}${pct}%`}
    >
      <span className="w-5 shrink-0 font-mono text-[10px] text-white/60">{String(index).padStart(2, "0")}</span>
      <span className="min-w-0 flex-1 truncate text-xs font-medium">{row.name}</span>
      <span className="shrink-0 font-mono text-xs font-bold">{pct > 0 ? "+" : ""}{pct}%</span>
    </div>
  );
}

/**
 * 固定高度的双榜热力矩阵：不依赖拖动/滚动，不假装一次展示全市场。
 * 每页同步展示 6 个领跌与 6 个领涨板块，5 页覆盖接口返回的两端各 30 个极值样本。
 */
export function SectorHeatBoard({ industry }: Props) {
  const [page, setPage] = useState(0);
  const leaders = industry.top;
  const decliners = useMemo(() => [...industry.bottom].reverse(), [industry.bottom]);
  const tailCount = Math.max(leaders.length, decliners.length);
  const pageCount = Math.max(Math.ceil(tailCount / PAGE_SIZE), 1);

  useEffect(() => {
    setPage((current) => Math.min(current, pageCount - 1));
  }, [pageCount]);

  const start = page * PAGE_SIZE;
  const visibleLeaders = leaders.slice(start, start + PAGE_SIZE);
  const visibleDecliners = decliners.slice(start, start + PAGE_SIZE);
  const bound = Math.max(
    ...leaders.map((row) => Math.abs(row.change_pct)),
    ...decliners.map((row) => Math.abs(row.change_pct)),
    0.01,
  );
  const shownEnd = Math.min(start + PAGE_SIZE, tailCount);

  return (
    <div className="flex min-h-[300px] flex-col" aria-label="板块涨跌双榜热力矩阵">
      <div className="mb-2 grid grid-cols-2 gap-3 text-xs font-medium">
        <div className="flex items-center justify-between px-1 text-success">
          <span>领跌板块</span><span className="text-[10px] text-muted-foreground">跌幅由强到弱</span>
        </div>
        <div className="flex items-center justify-between px-1 text-danger">
          <span>领涨板块</span><span className="text-[10px] text-muted-foreground">涨幅由强到弱</span>
        </div>
      </div>

      <div className="grid flex-1 grid-cols-2 gap-3">
        <div className="space-y-1.5">
          {visibleDecliners.map((row, index) => (
            <HeatRow key={`${row.code || row.name}-down`} row={row} bound={bound} index={start + index + 1} />
          ))}
        </div>
        <div className="space-y-1.5">
          {visibleLeaders.map((row, index) => (
            <HeatRow key={`${row.code || row.name}-up`} row={row} bound={bound} index={start + index + 1} />
          ))}
        </div>
      </div>

      <div className="mt-2 flex items-center justify-between gap-3 border-t border-border/40 pt-2 text-[11px] text-muted-foreground">
        <span>当前每侧 {start + 1}–{shownEnd} / {tailCount} 个极值板块 · 全市场 {industry.total} 个</span>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={() => setPage((current) => Math.max(current - 1, 0))}
            disabled={page === 0}
            className="inline-flex items-center gap-0.5 rounded border border-border/60 px-1.5 py-1 transition-colors hover:bg-muted/40 disabled:cursor-not-allowed disabled:opacity-30"
            aria-label="上一页"
          >
            <ChevronLeft className="h-3 w-3" />上一页
          </button>
          <span className="min-w-9 text-center font-mono">{page + 1}/{pageCount}</span>
          <button
            type="button"
            onClick={() => setPage((current) => Math.min(current + 1, pageCount - 1))}
            disabled={page >= pageCount - 1}
            className="inline-flex items-center gap-0.5 rounded border border-border/60 px-1.5 py-1 transition-colors hover:bg-muted/40 disabled:cursor-not-allowed disabled:opacity-30"
            aria-label="下一页"
          >
            下一页<ChevronRight className="h-3 w-3" />
          </button>
        </div>
      </div>
    </div>
  );
}
