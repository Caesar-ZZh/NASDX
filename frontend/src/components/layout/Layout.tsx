import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import {
  Activity, Radar, LayoutGrid, Wallet, Settings, Search, NotebookPen,
  Moon, Sun, ChevronsLeft, ChevronsRight, LineChart, Github, UserRound,
  Cog, Cpu, Database, Cable, Rocket, FlaskConical, Star, FileText, Swords,
  BarChart3, Gauge, Menu, X, Brain, Compass,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useDarkMode } from "@/hooks/useDarkMode";
import { storageGet, storageSet } from "@/lib/storage";

const APP_VERSION = "v0.3.0";
const REPO_URL = "https://github.com/Caesar-ZZh/cosmos";
// 作者联系方式
const X_URL = "https://github.com/Caesar-ZZh";
const MAIL_URL = "mailto:caesarzzh@163.com";

const NAV = [
  { to: "/daily-review", icon: Activity, label: "每日复盘" },
  { to: "/intel", icon: Radar, label: "资讯雷达" },
  { to: "/sectors", icon: LayoutGrid, label: "板块中心" },
  { to: "/stock-data", icon: Search, label: "个股数据" },
  { to: "/debate", icon: Swords, label: "多空辩论" },
  { to: "/deep-analysis", icon: Brain, label: "深度分析" },
  { to: "/watchlist", icon: Star, label: "自选股" },
  { to: "/cockpit", icon: Gauge, label: "实时驾驶舱" },
  { to: "/strategy-lab", icon: BarChart3, label: "策略实验室" },
  { to: "/portfolio-plan", icon: Compass, label: "投资路线" },
  { to: "/portfolio", icon: Wallet, label: "我的持仓" },
  { to: "/my-reports", icon: FileText, label: "我的研报" },
  { to: "/notes", icon: NotebookPen, label: "研究记录" },
  { to: "/settings", icon: Settings, label: "接入 AI" },
];

// 常看的板块，作为「板块中心」下的快捷入口（缩进显示）。
const SECTOR_LINKS = [
  { to: "/sectors/humanoid", icon: Cog, label: "人形机器人" },
  { to: "/sectors/ai-computing", icon: Cpu, label: "AI 算力" },
  { to: "/sectors/hbm", icon: Database, label: "HBM" },
  { to: "/sectors/cpo", icon: Cable, label: "光互联" },
  { to: "/sectors/business-space", icon: Rocket, label: "商业航天" },
  { to: "/sectors/ai-pharma", icon: FlaskConical, label: "生物医药" },
];

export function Layout() {
  const { pathname } = useLocation();
  const { dark, toggle } = useDarkMode();
  const [collapsed, setCollapsed] = useState(() => storageGet("vr-sidebar") === "collapsed");
  // 移动端抽屉：小屏下侧边栏改为覆盖层，默认收起。
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    storageSet("vr-sidebar", collapsed ? "collapsed" : "expanded");
  }, [collapsed]);

  // 路由变化后自动收起抽屉，避免点完导航还盖着内容。
  useEffect(() => {
    setDrawerOpen(false);
  }, [pathname]);

  // 抽屉打开时锁住背景滚动；关闭或离开时恢复。
  useEffect(() => {
    if (!drawerOpen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [drawerOpen]);

  // 抽屉内始终显示完整标签（宽度固定 w-64），只有桌面端才受 collapsed 影响。
  const showLabel = !collapsed || drawerOpen;

  return (
    <div className="flex h-screen">
      {/* 移动端顶部栏：桌面端隐藏（md 以上走侧边栏） */}
      <header className="glass fixed inset-x-0 top-0 z-20 flex h-14 items-center gap-2 rounded-none border-b border-border/50 px-3 md:hidden">
        <button
          type="button"
          onClick={() => setDrawerOpen(true)}
          aria-label="打开导航"
          className="rounded-lg p-2 text-foreground transition-colors hover:bg-muted/60"
        >
          <Menu className="h-5 w-5" />
        </button>
        {/* py-2：把它从 24px 撑到 40px，作为「回首页」入口更好点 */}
        <Link to="/daily-review" className="flex items-center gap-2 py-2">
          <LineChart className="h-5 w-5 shrink-0 text-primary text-glow" />
          <span className="font-extrabold tracking-tight text-primary">Cosmos</span>
        </Link>
        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            onClick={toggle}
            aria-label={dark ? "切换到亮色" : "切换到暗色"}
            className="rounded-lg p-2 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
          >
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
        </div>
      </header>

      {/* 抽屉遮罩：仅移动端、仅抽屉打开时渲染 */}
      {drawerOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50 backdrop-blur-sm md:hidden"
          onClick={() => setDrawerOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Sidebar：移动端为抽屉（fixed + 位移），桌面端为常驻侧栏（md:static） */}
      <aside
        className={cn(
          "glass z-40 m-2 flex shrink-0 flex-col rounded-2xl transition-transform duration-200",
          // 移动端：固定定位、固定宽度、默认移出屏幕左侧
          "fixed inset-y-2 left-2 w-64 shadow-2xl",
          drawerOpen ? "translate-x-0" : "-translate-x-full",
          // 桌面端：回到文档流，恢复折叠/展开宽度
          "md:static md:inset-auto md:translate-x-0 md:shadow-none",
          collapsed ? "md:w-14" : "md:w-60",
        )}
      >
        {/* Brand（仅桌面端显示，移动端已在顶部栏展示） */}
        <div className={cn("hidden border-b border-border/50 md:block", collapsed ? "flex justify-center p-3" : "p-4")}>
          <Link to="/daily-review" className={cn("flex items-center", collapsed ? "justify-center" : "gap-2")}>
            <LineChart className="h-6 w-6 shrink-0 text-primary text-glow" />
            {!collapsed && (
              <span className="text-lg font-extrabold tracking-tight text-primary">Cosmos</span>
            )}
          </Link>
          {!collapsed && <p className="mt-1 text-[11px] text-muted-foreground">个人 AI 投研系统 · A股/美股/港股</p>}
        </div>

        {/* 移动端抽屉标题栏（带关闭按钮） */}
        <div className="flex items-center justify-between border-b border-border/50 p-3 md:hidden">
          <span className="text-sm font-semibold text-muted-foreground">导航</span>
          <button
            type="button"
            onClick={() => setDrawerOpen(false)}
            aria-label="关闭导航"
            // p-2.5 + h-4 图标 = 36px 触控区，接近 44px 推荐值，避免手指点空
            className="rounded-lg p-2.5 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Nav */}
        <nav className={cn("flex-1 space-y-1 overflow-auto", collapsed && !drawerOpen ? "p-1.5" : "p-2.5")}>
          {NAV.map(({ to, icon: Icon, label }) => {
            const active = pathname === to;
            return (
              <div key={to}>
                <Link
                  to={to}
                  title={collapsed && !drawerOpen ? label : undefined}
                  className={cn(
                    "flex items-center rounded-lg transition-colors",
                    // 触控目标：移动端加大内边距，满足 44px 可点击区域
                    collapsed && !drawerOpen
                      ? "justify-center p-2.5 text-sm"
                      : "gap-2.5 px-3 py-3 text-base md:py-2.5 md:text-sm",
                    active
                      ? "bg-primary/15 font-medium text-primary shadow-glow"
                      : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
                  )}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  {showLabel && label}
                </Link>

                {/* 板块中心下方：常看板块的快捷入口（缩进） */}
                {to === "/sectors" && (
                  <div className={cn("mt-1 space-y-0.5", showLabel && "ml-4 border-l border-border/40 pl-1.5")}>
                    {SECTOR_LINKS.map(({ to: st, icon: SIcon, label: slabel }) => {
                      const sactive = pathname === st;
                      return (
                        <Link
                          key={st}
                          to={st}
                          title={collapsed && !drawerOpen ? slabel : undefined}
                          className={cn(
                            "flex items-center rounded-lg transition-colors",
                            collapsed && !drawerOpen
                              ? "justify-center p-2"
                              : "gap-2 px-2.5 py-2 text-sm md:py-1.5 md:text-[13px]",
                            sactive
                              ? "bg-primary/10 font-medium text-primary"
                              : "text-muted-foreground/80 hover:bg-muted/40 hover:text-foreground",
                          )}
                        >
                          <SIcon className="h-3.5 w-3.5 shrink-0" />
                          {showLabel && slabel}
                        </Link>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </nav>

        {/* Footer */}
        <div className={cn("border-t border-border/50", collapsed && !drawerOpen ? "flex flex-col items-center gap-2 p-2" : "space-y-2 p-3")}>
          {collapsed && !drawerOpen ? (
            <>
              <button onClick={toggle} className="rounded p-1.5 text-muted-foreground transition-colors hover:text-foreground" title={dark ? "亮色" : "暗色"}>
                {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              </button>
              <a href={X_URL} target="_blank" rel="noreferrer" className="rounded p-1.5 text-muted-foreground transition-colors hover:text-foreground" title="联系作者 · Caesar-ZZh">
                <UserRound className="h-4 w-4" />
              </a>
              <button onClick={() => setCollapsed(false)} className="rounded p-1.5 text-muted-foreground transition-colors hover:text-foreground" title="展开">
                <ChevronsRight className="h-4 w-4" />
              </button>
            </>
          ) : (
            <>
              <div className="flex items-center justify-between">
                {/* py-2：抽屉里这些只有 16px 高，手指点不准，撑到 32px+ */}
                <button onClick={toggle} className="flex items-center gap-1.5 py-2 text-xs text-muted-foreground transition-colors hover:text-foreground md:py-0">
                  {dark ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
                  {dark ? "亮色" : "暗色"}
                </button>
                <div className="flex items-center gap-2">
                  <a href={X_URL} target="_blank" rel="noreferrer" className="flex items-center p-2 text-muted-foreground transition-colors hover:text-foreground md:p-1" title="联系作者 · Caesar-ZZh">
                    <UserRound className="h-3.5 w-3.5" />
                  </a>
                  <a href={REPO_URL} target="_blank" rel="noreferrer" className="flex items-center p-2 text-muted-foreground transition-colors hover:text-foreground md:p-1" title="GitHub">
                    <Github className="h-3.5 w-3.5" />
                  </a>
                  <button onClick={() => setCollapsed(true)} className="hidden rounded p-1 text-muted-foreground transition-colors hover:text-foreground md:block" title="收起">
                    <ChevronsLeft className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
              <div className="flex items-center gap-1.5 text-[11px] text-primary/80">
                <span className="text-muted-foreground/60">联系作者</span>
                <a href={X_URL} target="_blank" rel="noreferrer" className="inline-flex items-center py-1.5 transition-colors hover:text-primary md:py-0">GitHub</a>
                <span className="text-muted-foreground/40">·</span>
                <a href={MAIL_URL} className="inline-flex items-center py-1.5 transition-colors hover:text-primary md:py-0">Email</a>
              </div>
              <p className="text-[11px] leading-relaxed text-muted-foreground/60">
                {APP_VERSION} · 不荐股 · 不预测 · 无倾向
              </p>
            </>
          )}
        </div>
      </aside>

      {/* Main：移动端留出顶部栏高度，桌面端不需要 */}
      <main className="flex-1 overflow-auto pt-14 md:pt-0">
        <div className="mx-auto max-w-6xl px-3 py-4 sm:px-4 sm:py-5 md:px-6 md:py-6">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
