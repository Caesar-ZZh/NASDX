// 多空辩论的全局状态（zustand，模块级）。
// 状态与流执行不挂在组件上：切走页面组件卸载后，store 保留、后端流继续，
// 切回来直接从 store 恢复，辩论不中断、不丢失。

import { create } from "zustand";

import { ApiError } from "@/lib/api";
import { debateStream, type DebateStage } from "@/lib/agents";
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
  controller: AbortController | null;

  setCode: (c: string) => void;
  setRounds: (r: number) => void;
  reset: () => void;
  start: () => Promise<void>;
  stop: () => void;
  save: () => void;
}

function emptyStages(): StageBox[] {
  return [];
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
  controller: null,

  setCode: (c) => set({ code: c }),
  setRounds: (r) => set({ rounds: r }),

  reset: () =>
    set({ status: "", progress: [], missing: [], stages: [], error: "", saved: false }),

  start: async () => {
    const { code, rounds } = get();
    const c = code.trim();
    if (!/^\d{6}$/.test(c)) {
      set({ error: "请输入 6 位 A 股代码" });
      return;
    }
    get().reset();
    set({ running: true, error: "" });
    const ctrl = new AbortController();
    set({ controller: ctrl });
    try {
      await debateStream(
        c,
        rounds,
        {
          onStatus: (msg) => set({ status: msg }),
          onDossierProgress: (title, ok, loaded, total) => {
            set((s) => ({
              status: `正在拉取客观事实底稿… ${loaded}/${total}`,
              progress: [...s.progress, { title, ok }],
            }));
          },
          onDossierReady: (_sections, miss) =>
            set({ missing: miss, status: "底稿就绪，辩论开始" }),
          onStageStart: (stage, label) =>
            set((s) => ({ stages: [...s.stages, { stage, label, content: "", done: false }] })),
          onDelta: (stage, text) =>
            set((s) => ({
              stages: s.stages.map((b) =>
                b.stage === stage && !b.done ? { ...b, content: b.content + text } : b
              ),
            })),
          onStageDone: (stage, _label, content) =>
            set((s) => ({
              stages: s.stages.map((b) =>
                b.stage === stage && !b.done ? { ...b, content, done: true } : b
              ),
            })),
          onError: (message, stage) =>
            set({ error: stage ? `${stage}：${message}` : message }),
        },
        ctrl.signal
      );
      set({ status: "辩论完成" });
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        set({ status: "已中止" });
      } else {
        set({ error: e instanceof ApiError ? e.message : String(e) });
      }
    } finally {
      set({ running: false, controller: null });
    }
  },

  stop: () => {
    get().controller?.abort();
    set({ running: false });
  },

  save: () => {
    const { stages, code } = get();
    const body = stages.map((s) => `## ${s.label}\n\n${s.content}`).join("\n\n---\n\n");
    addNote("多空辩论", `多空辩论 · ${code.trim()}`, body);
    set({ saved: true });
  },
}));

// 供组件复用（避免重复定义）
export { emptyStages };
