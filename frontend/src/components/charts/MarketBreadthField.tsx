import { useMemo, useState } from "react";
import type { MarketSentiment } from "@/lib/api";
import { chartColors } from "@/lib/chartTheme";

interface Props {
  sentiment: MarketSentiment;
}

interface BreadthGroup {
  key: "up" | "down" | "flat";
  label: string;
  count: number;
  ticks: number;
  color: string;
  start: number;
}

function allocateHundred(values: number[]): number[] {
  const total = values.reduce((sum, value) => sum + value, 0);
  if (total <= 0) return values.map(() => 0);
  const exact = values.map((value) => (value / total) * 100);
  const result = exact.map(Math.floor);
  let remaining = 100 - result.reduce((sum, value) => sum + value, 0);
  exact
    .map((value, index) => ({ index, fraction: value - result[index] }))
    .sort((a, b) => b.fraction - a.fraction || a.index - b.index)
    .forEach(({ index }) => {
      if (remaining > 0) {
        result[index] += 1;
        remaining -= 1;
      }
    });
  return result;
}

function polar(cx: number, cy: number, radius: number, angle: number): [number, number] {
  const radians = (angle * Math.PI) / 180;
  return [cx + Math.cos(radians) * radius, cy + Math.sin(radians) * radius];
}

// Lieflat catalog F4 · TICK DONUT
// Gallery: basics-gallery.html · “Where the traffic comes from”.
// The 100-tick clock, segmented unit encoding, tenth markers and staged reveal are preserved.
export function MarketBreadthField({ sentiment }: Props) {
  const c = chartColors();
  const [active, setActive] = useState<BreadthGroup["key"] | null>(null);
  const [pinned, setPinned] = useState<BreadthGroup["key"] | null>(null);
  const total = sentiment.up + sentiment.down + sentiment.flat;

  const groups = useMemo<BreadthGroup[]>(() => {
    const ticks = allocateHundred([sentiment.up, sentiment.down, sentiment.flat]);
    const base = [
      { key: "up" as const, label: "上涨", count: sentiment.up, ticks: ticks[0], color: c.up },
      { key: "down" as const, label: "下跌", count: sentiment.down, ticks: ticks[1], color: c.down },
      { key: "flat" as const, label: "平盘", count: sentiment.flat, ticks: ticks[2], color: c.muted },
    ];
    let start = 0;
    return base.map((group) => {
      const withStart = { ...group, start };
      start += group.ticks;
      return withStart;
    });
  }, [sentiment, c.up, c.down, c.muted]);

  const selectedKey = pinned ?? active;
  const selected = groups.find((group) => group.key === selectedKey);
  const toggle = (key: BreadthGroup["key"]) => setPinned((current) => current === key ? null : key);
  return (
    <div className="relative h-[236px] w-full" data-lieflat-template="F4 Tick Donut">
      <svg viewBox="0 0 400 236" className="h-full w-full" role="img" aria-label={`全市场 ${total} 家，上涨 ${sentiment.up} 家，下跌 ${sentiment.down} 家，平盘 ${sentiment.flat} 家`}>
        {groups.map((group, groupIndex) => {
          const midAngle = (group.start + group.ticks / 2) * 3.6 - 90;
          const [labelX, labelY] = polar(200, 104, 108, midAngle);
          const cosine = Math.cos((midAngle * Math.PI) / 180);
          const anchor = cosine > 0.25 ? "start" : cosine < -0.25 ? "end" : "middle";
          const dimmed = selectedKey !== null && selectedKey !== group.key;
          return (
            <g
              key={group.key}
              className="cursor-pointer"
              opacity={dimmed ? 0.2 : 1}
              onMouseEnter={() => setActive(group.key)}
              onMouseLeave={() => setActive(null)}
              onClick={() => toggle(group.key)}
            >
              <title>{`${group.label}：${group.count} 家（${total ? ((group.count / total) * 100).toFixed(1) : "0.0"}%）`}</title>
              {Array.from({ length: group.ticks }, (_, index) => {
                const tickIndex = group.start + index;
                const angle = tickIndex * 3.6 - 90;
                const length = 12 + ((tickIndex * 17 + groupIndex * 7) % 7);
                const [x1, y1] = polar(200, 104, 72, angle);
                const [x2, y2] = polar(200, 104, 72 + length, angle);
                return (
                  <line
                    key={`${group.key}-${index}`}
                    x1={x1}
                    y1={y1}
                    x2={x2}
                    y2={y2}
                    stroke={group.color}
                    strokeWidth={pinned === group.key ? 1.8 : 1.25}
                    strokeLinecap="round"
                  >
                    <animate attributeName="opacity" from="0" to="1" dur="420ms" begin={`${tickIndex * 8}ms`} fill="freeze" />
                  </line>
                );
              })}
              {Array.from({ length: group.ticks }, (_, index) => group.start + index)
                .filter((tickIndex) => tickIndex % 10 === 0)
                .map((tickIndex) => {
                  const [x, y] = polar(200, 104, 66, tickIndex * 3.6 - 90);
                  return <circle key={`marker-${tickIndex}`} cx={x} cy={y} r="1.1" fill={c.muted} />;
                })}
              <text x={labelX} y={labelY + 3} textAnchor={anchor} fill={group.color} fontSize="9" fontWeight="800">
                {group.label} · {group.ticks}%
              </text>
            </g>
          );
        })}

        <text x="200" y="99" textAnchor="middle" fill={c.foreground} fontSize="23" fontWeight="800">
          {selected ? selected.count.toLocaleString() : total.toLocaleString()}
        </text>
        <text x="200" y="116" textAnchor="middle" fill={selected?.color ?? c.muted} fontSize="8" fontWeight="700" letterSpacing="1">
          {selected ? `${selected.label} · CLICK TO UNPIN` : "全市场 · HOVER / CLICK"}
        </text>

        <text x="200" y="226" textAnchor="middle" fill={c.muted} opacity="0.75" fontSize="8" letterSpacing="1.1">
          ONE TICK = ONE PERCENT · DOT MARKS EVERY TENTH · READS CLOCKWISE
        </text>
      </svg>
      <div className="absolute inset-x-0 bottom-7 flex justify-center gap-1.5">
        {groups.map((group) => (
          <button
            key={group.key}
            type="button"
            aria-pressed={pinned === group.key}
            // 移动端 py-2（约 30px）：原来 21px 的手指按不准。加大后向上延伸，
            // 但徽章浮在图形底部留白处，不会压住散点。
            className="rounded border border-border/60 bg-background/80 px-2 py-2 text-[11px] transition-opacity hover:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary md:py-0.5 md:text-[10px]"
            style={{ color: group.color, opacity: selectedKey && selectedKey !== group.key ? 0.35 : 1 }}
            onMouseEnter={() => setActive(group.key)}
            onMouseLeave={() => setActive(null)}
            onFocus={() => setActive(group.key)}
            onBlur={() => setActive(null)}
            onClick={() => toggle(group.key)}
          >
            {group.label} {group.count.toLocaleString()}
          </button>
        ))}
      </div>
    </div>
  );
}
