import { useState } from "react";
import { RefreshCw, Loader2, AlertTriangle, Compass, ShieldAlert, ListChecks, GitBranch, CalendarClock, Database } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { api, type PortfolioPlan } from "@/lib/api";
import { cn } from "@/lib/utils";

const RISK_OPTIONS = [
  { key: "conservative", label: "保守", hint: "低仓位 · 强纪律" },
  { key: "balanced", label: "均衡", hint: "中性配置 · 攻守兼备" },
  { key: "aggressive", label: "进取", hint: "高仓位 · 高波动" },
];

// 红涨绿跌：posture 偏积极用红，保守用绿。
const postureColor = (p: string) => {
  if (/积极|进攻|做多|加仓|布局|上行|上涨/.test(p)) return "text-danger";
  if (/谨慎|减仓|回避|下行|下跌|防守/.test(p)) return "text-success";
  return "text-foreground";
};

function ListBlock({ title, items, icon: Icon, empty, color }: {
  title: string;
  items: any[];
  icon: any;
  empty: string;
  color?: string;
}) {
  return (
    <GlassCard className="p-4">
      <h4 className={cn("mb-2 flex items-center gap-1.5 text-sm font-semibold", color)}>
        <Icon className="h-4 w-4" /> {title}
      </h4>
      {!items || items.length === 0 ? (
        <p className="text-xs text-muted-foreground/60">{empty}</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {items.map((it, i) => (
            <li key={i} className="flex items-center justify-between gap-2 border-b border-border/20 py-1 last:border-0">
              <span className="min-w-0 truncate">{it.name ?? it.code ?? it}</span>
              {typeof it.score === "number" && (
                <span className="shrink-0 font-mono text-xs text-muted-foreground">{it.score.toFixed(1)}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </GlassCard>
  );
}

export function PortfolioPlanPage() {
  const [riskProfile, setRiskProfile] = useState("balanced");
  const [plan, setPlan] = useState<PortfolioPlan | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    setLoading(true);
    setErr(null);
    setPlan(null);
    try {
      const res = await api.portfolioPlan(riskProfile);
      setPlan(res.plan);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "生成失败");
    } finally {
      setLoading(false);
    }
  };

  const allocation = (plan?.allocation ?? {}) as Record<string, unknown>;

  return (
    <div>
      <PageHeader
        title="投资路线"
        subtitle="组合级规划：仓位框架 · 候选分层 · 情景推演 · 执行规则。纯本地确定性规则，不依赖 LLM"
      />

      <GlassCard className="mb-5">
        <div className="flex flex-wrap items-end gap-3">
          {RISK_OPTIONS.map((o) => (
            <button
              key={o.key}
              onClick={() => setRiskProfile(o.key)}
              className={cn(
                "rounded-lg border px-4 py-2 text-sm transition-colors",
                riskProfile === o.key
                  ? "border-primary/60 bg-primary/15 font-medium text-primary shadow-glow"
                  : "border-border/60 text-muted-foreground hover:text-foreground",
              )}
            >
              {o.label}
              <span className="ml-1.5 text-[11px] opacity-60">{o.hint}</span>
            </button>
          ))}
          <button
            onClick={run}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary/15 px-4 py-2 text-sm font-medium text-primary shadow-glow hover:bg-primary/25 disabled:opacity-50"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            {loading ? "生成中…" : "生成投资路线"}
          </button>
        </div>
        {err && (
          <p className="mt-3 flex items-center gap-1.5 text-sm text-warning">
            <AlertTriangle className="h-4 w-4" /> {err}
          </p>
        )}
      </GlassCard>

      {plan && (
        <>
          {/* 定调 + 仓位 */}
          <GlassCard className="mb-5" glow>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs text-muted-foreground">
                  {plan.risk_profile_label} · 生成于 {plan.generated_at.replace("T", " ").slice(0, 16)}
                </p>
                <h3 className={cn("mt-1 text-xl font-extrabold tracking-tight", postureColor(plan.posture))}>{plan.posture}</h3>
              </div>
              <div className="flex items-center gap-4">
                {(Object.entries(allocation) as [string, unknown][]).map(([k, v]) => (
                  <div key={k} className="text-center">
                    <p className="text-xs text-muted-foreground">{k}</p>
                    <p className="font-mono text-base font-bold">{String(v)}</p>
                  </div>
                ))}
              </div>
            </div>
            {plan.action_gate === "refresh_required" && (
              <p className="mt-3 flex items-center gap-1.5 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
                <AlertTriangle className="h-3.5 w-3.5" />
                缺少最新扫描产物（行情快照 / ETF50 / 个股扫描），当前为「先刷新，再决策」骨架。先在 Streamlit 端跑一次扫描后再生成。
              </p>
            )}
          </GlassCard>

          {/* 候选分层 */}
          <div className="mb-5 grid gap-3 md:grid-cols-2">
            <ListBlock title="ETF 主线候选" icon={Compass} items={plan.core_candidates} empty="暂无候选" color="text-danger" />
            <ListBlock title="个股卫星候选" icon={GitBranch} items={plan.satellite_candidates} empty="暂无候选" color="text-primary" />
            <ListBlock title="观察名单" icon={ListChecks} items={plan.watchlist} empty="暂无" />
            <ListBlock title="回避 / 减仓池" icon={ShieldAlert} items={plan.trim_or_avoid} empty="暂无" color="text-success" />
          </div>

          {/* 情景推演 + 执行规则 + 监控 */}
          <div className="mb-5 grid gap-3 md:grid-cols-3">
            <GlassCard className="p-4">
              <h4 className="mb-2 flex items-center gap-1.5 text-sm font-semibold"><GitBranch className="h-4 w-4 text-primary" /> 未来情景推演</h4>
              {/* API 返回的是 dict 数组 {scenario, trigger, action, position_rule}，按字段结构化渲染 */}
              <div className="space-y-3 text-xs">
                {plan.future_scenarios.map((s: any, i: number) => (
                  <div key={i} className="space-y-1">
                    {s.scenario && <div className="font-medium text-primary">{s.scenario}</div>}
                    {s.trigger && (
                      <div className="text-muted-foreground">
                        <span className="opacity-60">触发：</span>
                        {s.trigger}
                      </div>
                    )}
                    {s.action && (
                      <div className="text-foreground/90">
                        <span className="opacity-60">行动：</span>
                        {s.action}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </GlassCard>
            <GlassCard className="p-4">
              <h4 className="mb-2 flex items-center gap-1.5 text-sm font-semibold"><ListChecks className="h-4 w-4 text-primary" /> 执行规则</h4>
              <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">
                {plan.decision_rules.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </GlassCard>
            <GlassCard className="p-4">
              <h4 className="mb-2 flex items-center gap-1.5 text-sm font-semibold"><CalendarClock className="h-4 w-4 text-primary" /> 监控与复盘节奏</h4>
              <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">
                {plan.monitoring_checklist.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
                {plan.review_cadence.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </GlassCard>
          </div>

          {/* 数据质量 */}
          <GlassCard className="mb-5 p-4">
            <h4 className="mb-2 flex items-center gap-1.5 text-sm font-semibold"><Database className="h-4 w-4 text-primary" /> 数据质量</h4>
            <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
              {Object.entries(plan.data_quality ?? {}).map(([k, v]) => {
                const status = typeof v === "object" && v !== null ? (v as any).status : v;
                const ok = status === "ok" || status === "fresh";
                return (
                  <span key={k} className={cn("rounded-full border px-2 py-0.5", ok ? "border-success/40 text-success" : "border-warning/40 text-warning")}>
                    {k}: {String(status ?? "—")}
                  </span>
                );
              })}
            </div>
          </GlassCard>

          <p className="mb-4 text-center text-[11px] text-muted-foreground/60">{plan.disclaimer}</p>
        </>
      )}

      <Disclaimer />
    </div>
  );
}
