import { useState } from "react";
import { Search, Loader2, AlertTriangle, Swords, TrendingUp, TrendingDown, Minus } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { api, type AnalysisReport, type AnalysisVote } from "@/lib/api";
import { cn } from "@/lib/utils";

// A 股红涨绿跌：看多=红（涨色），看空=绿（跌色），中性=灰。
const signalMeta = (sig: string) => {
  if (sig === "bullish")
    return { label: "看多", cls: "text-danger border-danger/40 bg-danger/10", Icon: TrendingUp };
  if (sig === "bearish")
    return { label: "看空", cls: "text-success border-success/40 bg-success/10", Icon: TrendingDown };
  return { label: "中性", cls: "text-muted-foreground border-border/60 bg-muted/20", Icon: Minus };
};

const RISK_OPTIONS = [
  { key: "conservative", label: "保守" },
  { key: "balanced", label: "均衡" },
  { key: "aggressive", label: "进取" },
];

function ResearchBlock({ name, result }: { name: string; result: any }) {
  const sig = signalMeta(result?.signal);
  return (
    <GlassCard className="p-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h4 className="font-semibold">{name}</h4>
        {result?.signal && (
          <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium", sig.cls)}>
            <sig.Icon className="h-3 w-3" /> {sig.label}
            {typeof result?.confidence === "number" && (
              <span className="opacity-70">{(result.confidence * 100).toFixed(0)}%</span>
            )}
          </span>
        )}
      </div>
      {result?.conclusion && <p className="text-sm leading-relaxed text-foreground/90">{result.conclusion}</p>}
      {Array.isArray(result?.key_points) && result.key_points.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-muted-foreground">
          {result.key_points.map((k: string, i: number) => (
            <li key={i}>{k}</li>
          ))}
        </ul>
      )}
    </GlassCard>
  );
}

function VoteRow({ v }: { v: AnalysisVote }) {
  const sig = signalMeta(v.vote);
  return (
    <div className="flex items-start gap-3 border-b border-border/30 py-2 text-sm last:border-0">
      <span className="w-20 shrink-0 truncate font-medium">{v.agent_name}</span>
      <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium", sig.cls)}>
        <sig.Icon className="h-3 w-3" /> {sig.label}
      </span>
      <p className="flex-1 text-xs leading-relaxed text-muted-foreground">{v.reasoning}</p>
    </div>
  );
}

export function DeepAnalysis() {
  const [code, setCode] = useState("");
  const [riskProfile, setRiskProfile] = useState("balanced");
  const [depth, setDepth] = useState("full");
  const [report, setReport] = useState<AnalysisReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    if (!/^\d{6}$/.test(code.trim())) {
      setErr("请输入 6 位股票代码，如 600519");
      return;
    }
    setLoading(true);
    setErr(null);
    setReport(null);
    try {
      const res = await api.analysis(code.trim(), { risk_profile: riskProfile, depth });
      setReport(res.report);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "分析失败");
    } finally {
      setLoading(false);
    }
  };

  const finalSig = report ? signalMeta(report.final_signal) : null;

  return (
    <div>
      <PageHeader
        title="深度分析"
        subtitle="5 个专家 Agent 并行研究 → 多空辩论 → 综合研判，输出完整深度报告与操作建议"
      />

      <GlassCard className="mb-5">
        <div className="flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="mb-1 block text-xs text-muted-foreground">股票代码</span>
            <input
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              onKeyDown={(e) => e.key === "Enter" && run()}
              placeholder="如 600519"
              className="w-40 rounded-lg border border-border bg-background/50 px-3 py-2 text-sm outline-none focus:border-primary/50"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-muted-foreground">风险画像</span>
            <select
              value={riskProfile}
              onChange={(e) => setRiskProfile(e.target.value)}
              className="rounded-lg border border-border bg-background/50 px-3 py-2 text-sm outline-none focus:border-primary/50"
            >
              {RISK_OPTIONS.map((o) => (
                <option key={o.key} value={o.key}>{o.label}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-muted-foreground">深度</span>
            <select
              value={depth}
              onChange={(e) => setDepth(e.target.value)}
              className="rounded-lg border border-border bg-background/50 px-3 py-2 text-sm outline-none focus:border-primary/50"
            >
              <option value="full">完整分析</option>
              <option value="intraday">盘中增量</option>
              <option value="refresh">定向刷新</option>
            </select>
          </label>
          <button
            onClick={run}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary/15 px-4 py-2 text-sm font-medium text-primary shadow-glow hover:bg-primary/25 disabled:opacity-50"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
            {loading ? "分析中…" : "开始分析"}
          </button>
        </div>
        {loading && (
          <p className="mt-3 text-xs text-muted-foreground">
            正在执行 5 Agent 研究 + 多空辩论 + 综合研判，通常 30–120 秒（有缓存时更快）…
          </p>
        )}
        {err && (
          <p className="mt-3 flex items-center gap-1.5 text-sm text-warning">
            <AlertTriangle className="h-4 w-4" /> {err}
          </p>
        )}
      </GlassCard>

      {report && finalSig && (
        <>
          {/* 顶部信号卡 */}
          <GlassCard className="mb-5" glow>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs text-muted-foreground">
                  {report.stock_code} · {report.stock_name} · {report.date} · 深度 {report.analysis_depth}
                </p>
                <h3 className="mt-1 text-2xl font-extrabold tracking-tight">{report.stock_name || report.stock_code}</h3>
              </div>
              <div className="flex items-center gap-3">
                <span className={cn("inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-bold", finalSig.cls)}>
                  <finalSig.Icon className="h-4 w-4" /> {finalSig.label}
                </span>
                <div className="text-right">
                  <p className="text-xs text-muted-foreground">辩论看多比例</p>
                  <p className="font-mono text-lg font-bold text-danger">{Math.round(report.bullish_pct * 100)}%</p>
                </div>
              </div>
            </div>
            {report.summary && (
              <p className="mt-4 border-t border-border/30 pt-3 text-sm leading-relaxed text-foreground/90">
                {report.summary}
              </p>
            )}
          </GlassCard>

          {/* 操作建议 + 决策计划 */}
          {report.operation_advice && (
            <GlassCard className="mb-5 p-4">
              <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold">
                <Swords className="h-4 w-4 text-primary" /> 操作建议
              </h3>
              <p className="text-sm leading-relaxed text-foreground/90">{report.operation_advice}</p>
            </GlassCard>
          )}

          {/* 各维度研究结果 */}
          <div className="mb-5 grid gap-3 md:grid-cols-2">
            {Object.entries(report.research_results || {}).map(([name, res]) => (
              <ResearchBlock key={name} name={name} result={res} />
            ))}
          </div>

          {/* 辩论投票 */}
          {Array.isArray(report.votes) && report.votes.length > 0 && (
            <GlassCard className="mb-5 p-4">
              <h3 className="mb-1 flex items-center gap-1.5 text-sm font-semibold">
                <TrendingUp className="h-4 w-4 text-primary" /> 辩论投票
              </h3>
              {report.votes.map((v, i) => (
                <VoteRow key={i} v={v} />
              ))}
            </GlassCard>
          )}
        </>
      )}

      <Disclaimer />
    </div>
  );
}
