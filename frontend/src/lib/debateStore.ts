// 多空辩论的全局状态（zustand，模块级）。
// 辩论跑在服务端后台任务里（/api/jobs/debate），本 store 只负责：
//   1. 提交任务，把 job_id 存 localStorage；
//   2. 轮询任务事件，增量重建「角色发言」面板；
//   3. 切走再切回来（甚至刷新浏览器）都从 localStorage 恢复，不中断不丢失。
// 这替代了旧的 NDJSON 流式方案——旧方案把结果绑死在当前页面的连接上。

import { create } from "zustand";

import { ApiError, api, type JobEvent } from "@/lib/api";
import { requireLlm, type DebateStage } from "@/lib/agents";
import { addNote } from "@/lib/notes";

export interface StageBox {
  stage: DebateStage;
  label: string;
  content: string;
  done: boolean;
}

export interface DebateProgress {
  title: string;
  ok: boolean;
}

interface DebateState {
  code: string;
  rounds: number;
  running: boolean;
  status: string;
  progress: DebateProgress[];
  missing: string[];
  stages: StageBox[];
  error: string;
  saved: boolean;
  jobId: string | null;

  setCode: (c: string) => void;
  setRounds: (r: number) => void;
  reset: () => void;
  start: () => Promise<void>;
  resume: () => void;
  stop: () => void;
  save: () => void;
}

// ---- localStorage 槽位：与 useBackgroundJob 共用同一前缀，互不干扰 ----

const STORAGE_KEY = "nasdx-job:debate";

function readStored(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStored(jobId: string | null) {
  try {
    if (jobId) localStorage.setItem(STORAGE_KEY, jobId);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* 隐私模式等场景 */
  }
}

// ---- 轮询循环（模块级，不依赖 React 组件生命周期）----

let pollActive = false;
let cursorRef = 0;
const POLL_MS = 1500;

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

/** 把一条 job 事件落进 store。 */
function dispatchEvent(ev: JobEvent) {
  const set = useDebateStore.setState;
  switch (ev.type) {
    case "status":
      set({ status: String(ev.message ?? "") });
      break;
    case "dossier_progress":
      set((s) => ({
        ...s,
        status: `正在拉取客观事实底稿… ${ev.loaded}/${ev.total}`,
        progress: [...s.progress, { title: String(ev.title ?? ""), ok: !!ev.ok }],
      }));
      break;
    case "dossier":
      set({ missing: Array.isArray(ev.missing) ? (ev.missing as string[]) : [], status: "底稿就绪，辩论开始" });
      break;
    case "stage":
      set((s) => ({
        ...s,
        stages: [...s.stages, { stage: ev.stage as DebateStage, label: String(ev.label ?? ""), content: "", done: false }],
      }));
      break;
    case "delta":
      set((s) => ({
        ...s,
        stages: s.stages.map((b) =>
          b.stage === ev.stage && !b.done ? { ...b, content: b.content + String(ev.text ?? "") } : b
        ),
      }));
      break;
    case "stage_done":
      set((s) => ({
        ...s,
        stages: s.stages.map((b) =>
          b.stage === ev.stage && !b.done ? { ...b, content: String(ev.content ?? ""), done: true } : b
        ),
      }));
      break;
    case "error": {
      const message = String(ev.message ?? "辩论出错");
      const stage = ev.stage ? String(ev.stage) : "";
      set({ error: stage ? `${stage}：${message}` : message });
      break;
    }
    case "done":
      set({ status: "辩论完成" });
      break;
  }
}

async function pollLoop(jobId: string) {
  if (pollActive) return;
  pollActive = true;
  cursorRef = 0;
  try {
    while (true) {
      const st = useDebateStore.getState();
      if (st.jobId !== jobId) return; // 已被替换 / 已中止 / 已清空

      let snap;
      try {
        snap = await api.getJob(jobId, cursorRef);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          writeStored(null);
          useDebateStore.setState({ running: false, jobId: null, error: "任务已过期（后端可能重启过），请重新开始" });
          return;
        }
        // 网络抖动：退避重试，不判死
        await sleep(POLL_MS);
        continue;
      }

      // 恢复场景：从任务参数里找回代码，让输入框显示的是在跑的那只
      if (snap.params?.code && !useDebateStore.getState().code) {
        useDebateStore.setState({ code: String(snap.params.code) });
      }
      // 游标失效 → 全量重放：清空本地累积，按事件流重建
      if (snap.replay) {
        cursorRef = 0;
        useDebateStore.setState({ stages: [], progress: [], missing: [] });
      }
      for (const ev of snap.events) dispatchEvent(ev);
      cursorRef = snap.cursor;

      const now = useDebateStore.getState();
      if (now.jobId !== jobId) return;

      if (snap.status === "done") {
        writeStored(null);
        useDebateStore.setState({ running: false, jobId: null, status: "辩论完成" });
        return;
      }
      if (snap.status === "error") {
        writeStored(null);
        useDebateStore.setState({ running: false, jobId: null, error: snap.error || "辩论失败" });
        return;
      }
      if (snap.status === "cancelled") {
        writeStored(null);
        useDebateStore.setState({ running: false, jobId: null, status: "已中止" });
        return;
      }
      await sleep(POLL_MS);
    }
  } finally {
    pollActive = false;
  }
}

export const useDebateStore = create<DebateState>()((set, get) => ({
  code: "",
  rounds: 1,
  running: false,
  status: "",
  progress: [],
  missing: [],
  stages: [],
  error: "",
  saved: false,
  jobId: null,

  setCode: (c) => set({ code: c }),
  setRounds: (r) => set({ rounds: r }),

  reset: () => {
    writeStored(null);
    set({ status: "", progress: [], missing: [], stages: [], error: "", saved: false, jobId: null });
  },

  start: async () => {
    const { code, rounds } = get();
    const c = code.trim();
    if (!/^\d{6}$/.test(c)) {
      set({ error: "请输入 6 位 A 股代码" });
      return;
    }
    get().reset();
    set({ running: true, error: "" });
    try {
      const res = await api.startDebateJob(c, rounds, requireLlm());
      set({ jobId: res.job_id });
      writeStored(res.job_id);
      void pollLoop(res.job_id);
    } catch (e) {
      set({ running: false, error: e instanceof ApiError ? e.message : String(e) });
    }
  },

  resume: () => {
    const stored = readStored();
    if (!stored || get().jobId) return;
    set({ jobId: stored, running: true, error: "" });
    void pollLoop(stored);
  },

  stop: () => {
    const id = get().jobId;
    if (id) {
      // 请求取消；不阻塞 UI，轮询看到 cancelled 后自行收敛
      void api.cancelJob(id).catch(() => {});
    }
    writeStored(null);
    set({ running: false, status: "已中止", jobId: null });
  },

  save: () => {
    const { stages, code } = get();
    const body = stages.map((s) => `## ${s.label}\n\n${s.content}`).join("\n\n---\n\n");
    addNote("多空辩论", `多空辩论 · ${code.trim()}`, body);
    set({ saved: true });
  },
}));

// 供组件复用（避免重复定义）
export function emptyStages(): StageBox[] {
  return [];
}
