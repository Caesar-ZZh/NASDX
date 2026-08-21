// 从 CSS 变量读取当前主题配色，供 echarts 图表使用（自动跟随 dark / light）。
// 这些 CSS 变量是 "H S% L%" 形式的 HSL 三元组，需包成 hsl(...) 才能给 echarts 用。

function readVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (!raw) return fallback;
  // HSL 三元组直接包成 hsl(...)；若已经是合法颜色则原样返回。
  return raw.includes("%") ? `hsl(${raw})` : raw;
}

export interface ChartColors {
  up: string;        // 涨 = 红（A股惯例）
  down: string;      // 跌 = 绿
  primary: string;   // 暖橙主色
  accent: string;    // 橙强调
  foreground: string;
  muted: string;     // 次要文字
  grid: string;      // 分割线
  axis: string;      // 坐标轴
}

export function chartColors(): ChartColors {
  return {
    up: readVar("--danger", "#ef4444"),
    down: readVar("--success", "#22c55e"),
    primary: readVar("--primary", "#f35d2b"),
    accent: readVar("--accent", "#fa832e"),
    foreground: readVar("--foreground", "#e7eef7"),
    muted: readVar("--chart-text", "#8a93a3"),
    grid: readVar("--chart-grid", "rgba(255,255,255,0.06)"),
    axis: readVar("--chart-axis", "rgba(255,255,255,0.12)"),
  };
}

/** 涨红跌绿灰平：和页面 pctColor 同一套语义，供图表调用。 */
export function pctColor(p: number, c: ChartColors): string {
  return p > 0 ? c.up : p < 0 ? c.down : c.muted;
}
