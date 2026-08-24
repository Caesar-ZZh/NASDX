import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createServer } from "vite";

const source = async (relativePath) =>
  readFile(new URL(`../src/${relativePath}`, import.meta.url), "utf8");

test("workspace state uses safe versioned local storage with bounded presets", async () => {
  const store = await source("lib/workspaceState.ts");

  assert.match(store, /from "@\/lib\/storage"/);
  assert.match(store, /storageGet/);
  assert.match(store, /storageSet/);
  assert.doesNotMatch(store, /\blocalStorage\.(get|set|remove)Item\b/);
  assert.match(store, /STRATEGY_WORKSPACE_KEY\s*=\s*"cosmos-strategy-lab-v1"/);
  assert.match(store, /STRATEGY_PRESETS_KEY\s*=\s*"cosmos-strategy-presets-v1"/);
  assert.match(store, /MAX_STRATEGY_PRESETS\s*=\s*20/);
  assert.match(store, /try\s*\{[\s\S]*JSON\.parse[\s\S]*\}\s*catch/);
  assert.match(store, /version:\s*1/);
});

test("strategy lab restores last run and manages named combinations", async () => {
  const page = await source("pages/StrategyLab.tsx");

  assert.match(page, /loadStrategyWorkspace/);
  assert.match(page, /saveStrategyWorkspace/);
  assert.match(page, /loadStrategyPresets/);
  assert.match(page, /upsertStrategyPreset/);
  assert.match(page, /删除组合/);
  assert.match(page, /保存组合/);
  assert.match(page, /已从本机恢复/);
});

test("stock data restores the last successful symbol without persisting responses", async () => {
  const page = await source("pages/StockData.tsx");
  const store = await source("lib/workspaceState.ts");

  assert.match(page, /loadLastStockCode/);
  assert.match(page, /saveLastStockCode/);
  assert.match(page, /useEffect/);
  assert.match(page, /!val\s*&&\s*!gstock\s*&&\s*!err\s*&&\s*!loading/);
  assert.match(store, /cosmos-stock-data-last-code-v1/);
  assert.doesNotMatch(store, /Valuation|Financials|Announcement/);
});

test("intel restores the main tab and investment-news sector", async () => {
  const page = await source("pages/Intel.tsx");
  const store = await source("lib/workspaceState.ts");

  assert.match(page, /loadIntelView/);
  assert.match(page, /saveIntelView/);
  assert.match(page, /savedSector/);
  assert.match(store, /cosmos-intel-view-v1/);
});

test("workspace persistence behavior survives malformed and repeated local writes", async (t) => {
  const values = new Map();
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const fakeStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
    clear: () => values.clear(),
    key: (index) => [...values.keys()][index] ?? null,
    get length() { return values.size; },
  };
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: fakeStorage });

  let vite;
  t.after(async () => {
    await vite?.close();
    if (originalStorage) Object.defineProperty(globalThis, "localStorage", originalStorage);
    else delete globalThis.localStorage;
  });
  vite = await createServer({ server: { middlewareMode: true }, appType: "custom", logLevel: "silent" });
  const workspace = await vite.ssrLoadModule("/src/lib/workspaceState.ts");
  const defaultDraft = {
    universe: "510300, 510500",
    strategies: ["momentum", "mean_reversion"],
    start: "2025-08-24",
    end: "2026-08-24",
    rebalance: "W",
    topN: 2,
  };
  const result = {
    result_type: "objective_calculation",
    notice: "test",
    parameters: {
      universe: ["510300", "510500"], strategies: ["momentum"], start: defaultDraft.start,
      end: defaultDraft.end, initial_capital: 100000, rebalance: "W", top_n: 2,
    },
    coverage: { requested: 2, available: 2, missing: [] },
    strategies: [],
  };

  await t.test("malformed JSON and invalid fields fall back without crashing", () => {
    values.clear();
    values.set(workspace.STRATEGY_WORKSPACE_KEY, "{broken");
    assert.deepEqual(workspace.loadStrategyWorkspace(defaultDraft), {
      draft: defaultDraft, result: null, restored: false,
    });

    values.set(workspace.STRATEGY_WORKSPACE_KEY, JSON.stringify({
      version: 1,
      draft: { universe: 99, strategies: ["unknown"], start: "bad", end: null, rebalance: "Q", topN: 99 },
      result: { result_type: "wrong" },
    }));
    const restored = workspace.loadStrategyWorkspace(defaultDraft);
    assert.equal(restored.draft.universe, defaultDraft.universe);
    assert.deepEqual(restored.draft.strategies, []);
    assert.equal(restored.draft.start, defaultDraft.start);
    assert.equal(restored.draft.end, defaultDraft.end);
    assert.equal(restored.draft.rebalance, defaultDraft.rebalance);
    assert.equal(restored.draft.topN, defaultDraft.topN);
    assert.equal(restored.result, null);
  });

  await t.test("workspace draft and successful result round trip", () => {
    values.clear();
    workspace.saveStrategyWorkspace(defaultDraft, result);
    assert.deepEqual(workspace.loadStrategyWorkspace(defaultDraft), {
      draft: defaultDraft, result, restored: true,
    });
  });

  await t.test("named presets stay bounded to twenty", () => {
    values.clear();
    let presets = [];
    for (let index = 0; index < 25; index += 1) {
      presets = workspace.upsertStrategyPreset(presets, `组合 ${index}`, defaultDraft);
    }
    assert.equal(presets.length, workspace.MAX_STRATEGY_PRESETS);
    workspace.saveStrategyPresets(presets);
    assert.equal(workspace.loadStrategyPresets(defaultDraft).length, workspace.MAX_STRATEGY_PRESETS);
  });

  await t.test("Intel tab and sector updates merge instead of overwriting", () => {
    values.clear();
    workspace.saveIntelView({ sector: "robotics" });
    workspace.saveIntelView({ tab: "filings" });
    assert.deepEqual(workspace.loadIntelView(), { tab: "filings", sector: "robotics" });
  });
});
