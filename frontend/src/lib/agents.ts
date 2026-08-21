// 多 agent 能力的前端客户端：多空辩论 + 反思审计。
// 两者都走后端 NDJSON 流；模型配置沿用「接入 AI」里存的那一份（用户自己的 key / 本机 CLI）。

import { ApiError } from "@/lib/api";
import { loadLlm } from "@/lib/llm";
import { streamNdjson, type NdjsonEvent } from "@/lib/ndjson";

export type DebateStage = "bull" | "bear" | "bull_rebut" | "bear_rebut" | "referee";

export interface DebateHandlers {
  onStatus?: (message: string) => void;
  onDossierProgress?: (title: string, ok: boolean, loaded: number, total: number) => void;
  onDossierReady?: (sections: { title: string; tool: string }[], missing: string[]) => void;
  onStageStart?: (stage: DebateStage, label: string) => void;
  onDelta?: (stage: DebateStage, text: string) => void;
  onStageDone?: (stage: DebateStage, label: string, content: string) => void;
  onError?: (message: string, stage?: DebateStage) => void;
}

function requireLlm() {
  const llm = loadLlm();
  if (!llm) throw new ApiError("尚未接入 AI，请先在「接入 AI」里配置", 400);
  return llm;
}

function dispatchDebate(ev: NdjsonEvent, h: DebateHandlers) {
  switch (ev.type) {
    case "status":
      h.onStatus?.(ev.message);
      break;
    case "dossier_progress":
      h.onDossierProgress?.(ev.title, ev.ok, ev.loaded, ev.total);
      break;
    case "dossier":
      h.onDossierReady?.(ev.sections || [], ev.missing || []);
      break;
    case "stage":
      h.onStageStart?.(ev.stage, ev.label);
      break;
    case "delta":
      h.onDelta?.(ev.stage, ev.text);
      break;
    case "stage_done":
      h.onStageDone?.(ev.stage, ev.label, ev.content);
      break;
    case "error":
      h.onError?.(ev.message, ev.stage);
      break;
  }
}

/** 跑一场多空辩论。rounds=2 时多空各多一轮交叉反驳。 */
export async function debateStream(
  code: string,
  rounds: number,
  handlers: DebateHandlers = {},
  signal?: AbortSignal,
): Promise<void> {
  const llm = requireLlm();
  await streamNdjson("/api/debate", { code, rounds, llm }, (ev) => dispatchDebate(ev, handlers), signal);
}

export interface ReflectHandlers {
  onStatus?: (message: string) => void;
  onDelta?: (text: string) => void;
  onDone?: (content: string, truncated: boolean) => void;
  onError?: (message: string) => void;
}

/** 对一段已写好的分析做推理审计。 */
export async function reflectStream(
  source: string,
  title: string,
  handlers: ReflectHandlers = {},
  signal?: AbortSignal,
): Promise<void> {
  const llm = requireLlm();
  await streamNdjson("/api/reflect", { source, title, llm }, (ev) => {
    if (ev.type === "status") handlers.onStatus?.(ev.message);
    else if (ev.type === "delta") handlers.onDelta?.(ev.text);
    else if (ev.type === "done") handlers.onDone?.(ev.content, !!ev.truncated);
    else if (ev.type === "error") handlers.onError?.(ev.message);
  }, signal);
}
