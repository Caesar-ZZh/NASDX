import type { QuantBacktestRequest, QuantBacktestResult } from "@/lib/api";
import { storageGet, storageSet } from "@/lib/storage";

export const STRATEGY_WORKSPACE_KEY = "cosmos-strategy-lab-v1";
export const STRATEGY_PRESETS_KEY = "cosmos-strategy-presets-v1";
export const MAX_STRATEGY_PRESETS = 20;

const STOCK_DATA_KEY = "cosmos-stock-data-last-code-v1";
const INTEL_VIEW_KEY = "cosmos-intel-view-v1";
const STRATEGY_KEYS = new Set(["momentum", "mean_reversion", "factor_rank"]);
const REBALANCE_KEYS = new Set(["D", "W", "M"]);
const INTEL_TABS = new Set(["events", "filings", "news", "investment-news"]);

export type StrategyKey = QuantBacktestRequest["strategies"][number];

export interface StrategyDraft {
  universe: string;
  strategies: StrategyKey[];
  start: string;
  end: string;
  rebalance: string;
  topN: number;
}

export interface StrategyPreset {
  id: string;
  name: string;
  draft: StrategyDraft;
  updatedAt: number;
}

export interface IntelView {
  tab: string;
  sector: string;
}

const parseJson = (raw: string | null): unknown => {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const validDate = (value: unknown): value is string =>
  typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);

const normalizeDraft = (value: unknown, fallback: StrategyDraft): StrategyDraft => {
  if (!isRecord(value)) return fallback;
  const strategies = Array.isArray(value.strategies)
    ? value.strategies.filter((item): item is StrategyKey => typeof item === "string" && STRATEGY_KEYS.has(item))
    : fallback.strategies;
  const topN = typeof value.topN === "number" && Number.isInteger(value.topN) && value.topN >= 1 && value.topN <= 10
    ? value.topN
    : fallback.topN;
  return {
    universe: typeof value.universe === "string" ? value.universe.slice(0, 240) : fallback.universe,
    strategies,
    start: validDate(value.start) ? value.start : fallback.start,
    end: validDate(value.end) ? value.end : fallback.end,
    rebalance: typeof value.rebalance === "string" && REBALANCE_KEYS.has(value.rebalance) ? value.rebalance : fallback.rebalance,
    topN,
  };
};

const validBacktest = (value: unknown): value is QuantBacktestResult =>
  isRecord(value)
  && value.result_type === "objective_calculation"
  && isRecord(value.parameters)
  && isRecord(value.coverage)
  && Array.isArray(value.strategies);

export function loadStrategyWorkspace(defaultDraft: StrategyDraft): {
  draft: StrategyDraft;
  result: QuantBacktestResult | null;
  restored: boolean;
} {
  const saved = parseJson(storageGet(STRATEGY_WORKSPACE_KEY));
  if (!isRecord(saved) || saved.version !== 1) {
    return { draft: defaultDraft, result: null, restored: false };
  }
  return {
    draft: normalizeDraft(saved.draft, defaultDraft),
    result: validBacktest(saved.result) ? saved.result : null,
    restored: true,
  };
}

export function saveStrategyWorkspace(draft: StrategyDraft, result: QuantBacktestResult | null): void {
  storageSet(STRATEGY_WORKSPACE_KEY, JSON.stringify({ version: 1, draft, result }));
}

export function loadStrategyPresets(defaultDraft: StrategyDraft): StrategyPreset[] {
  const saved = parseJson(storageGet(STRATEGY_PRESETS_KEY));
  if (!isRecord(saved) || saved.version !== 1 || !Array.isArray(saved.presets)) return [];
  return saved.presets.flatMap((item): StrategyPreset[] => {
    if (!isRecord(item) || typeof item.id !== "string" || typeof item.name !== "string") return [];
    return [{
      id: item.id,
      name: item.name.slice(0, 40),
      draft: normalizeDraft(item.draft, defaultDraft),
      updatedAt: typeof item.updatedAt === "number" ? item.updatedAt : 0,
    }];
  }).slice(0, MAX_STRATEGY_PRESETS);
}

export function saveStrategyPresets(presets: StrategyPreset[]): void {
  storageSet(STRATEGY_PRESETS_KEY, JSON.stringify({ version: 1, presets: presets.slice(0, MAX_STRATEGY_PRESETS) }));
}

export function upsertStrategyPreset(
  presets: StrategyPreset[],
  name: string,
  draft: StrategyDraft,
): StrategyPreset[] {
  const normalizedName = name.trim().slice(0, 40);
  if (!normalizedName) return presets;
  const existing = presets.find((item) => item.name.toLocaleLowerCase() === normalizedName.toLocaleLowerCase());
  const next: StrategyPreset = {
    id: existing?.id ?? `strategy-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
    name: normalizedName,
    draft,
    updatedAt: Date.now(),
  };
  return [next, ...presets.filter((item) => item.id !== next.id)].slice(0, MAX_STRATEGY_PRESETS);
}

export function deleteStrategyPreset(presets: StrategyPreset[], id: string): StrategyPreset[] {
  return presets.filter((item) => item.id !== id);
}

export function loadLastStockCode(): string {
  const saved = parseJson(storageGet(STOCK_DATA_KEY));
  if (!isRecord(saved) || saved.version !== 1 || typeof saved.code !== "string") return "";
  return /^[A-Z0-9.]{1,12}$/.test(saved.code) ? saved.code : "";
}

export function saveLastStockCode(code: string): void {
  const normalized = code.trim().toUpperCase();
  if (/^[A-Z0-9.]{1,12}$/.test(normalized)) {
    storageSet(STOCK_DATA_KEY, JSON.stringify({ version: 1, code: normalized }));
  }
}

export function loadIntelView(): IntelView {
  const defaults = { tab: "investment-news", sector: "ai" };
  const saved = parseJson(storageGet(INTEL_VIEW_KEY));
  if (!isRecord(saved) || saved.version !== 1) return defaults;
  return {
    tab: typeof saved.tab === "string" && INTEL_TABS.has(saved.tab) ? saved.tab : defaults.tab,
    sector: typeof saved.sector === "string" && /^[a-z0-9-]{1,40}$/.test(saved.sector) ? saved.sector : defaults.sector,
  };
}

export function saveIntelView(patch: Partial<IntelView>): void {
  const current = loadIntelView();
  const next = { ...current, ...patch };
  storageSet(INTEL_VIEW_KEY, JSON.stringify({ version: 1, ...next }));
}
