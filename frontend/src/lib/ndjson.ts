// 通用 NDJSON 流读取器 —— 后端的 /api/debate、/api/reflect 都用「每行一个 JSON 事件」推送。
// 抽出来是因为多 agent 流程的事件类型比对话多（阶段、进度、分角色增量），
// 各页面只关心事件本身，不该各写一遍拆行/解码逻辑。

import { ApiError, authHeaders } from "@/lib/api";

export type NdjsonEvent = Record<string, any>;

/**
 * POST 一个 JSON body，按行消费 NDJSON 事件流。
 * - 配置类错误（400/401）在流开始前抛 ApiError，调用方可直接提示用户去配置。
 * - 流内的 {type:"error"} 事件交给 onEvent 自行处理（单个角色失败不必中断整场）。
 */
export async function streamNdjson(
  url: string,
  body: unknown,
  onEvent: (ev: NdjsonEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError("连接不到后端，请先启动 backend（uvicorn app:app --port 8900）", 0);
  }

  if (!resp.ok) {
    let detail: any = null;
    try { detail = await resp.json(); } catch { /* 无 JSON body 就用状态码兜底 */ }
    if (resp.status === 401) {
      throw new ApiError("后端开启了访问鉴权（VR_API_KEY）：请在「接入 AI」页底部填写后端访问密钥", 401);
    }
    throw new ApiError(detail?.detail || `HTTP ${resp.status}`, resp.status);
  }
  if (!resp.body) throw new ApiError("后端无响应流", 502);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";   // 末尾可能是半行，留到下一块
    for (const line of lines) {
      const t = line.trim();
      if (!t) continue;
      try { onEvent(JSON.parse(t)); } catch { /* 半截行或脏行，跳过 */ }
    }
  }
}
