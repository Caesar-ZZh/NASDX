import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";


const source = async (relativePath) =>
  readFile(new URL(`../src/${relativePath}`, import.meta.url), "utf8");


test("strategy lab is reachable from navigation and router", async () => {
  const layout = await source("components/layout/Layout.tsx");
  const router = await source("router.tsx");

  assert.match(layout, /\/strategy-lab/);
  assert.match(layout, /策略实验室/);
  assert.match(router, /StrategyLab/);
  assert.match(router, /\/strategy-lab/);
});


test("strategy lab exposes backtest comparison and ETF50 scoring", async () => {
  const api = await source("lib/api.ts");
  const page = await source("pages/StrategyLab.tsx");

  assert.match(api, /quantBacktest/);
  assert.match(api, /\/quant\/backtest/);
  assert.match(api, /quantEtf50/);
  assert.match(api, /\/quant\/etf50/);
  assert.match(page, /EChart/);
  assert.match(page, /动量策略/);
  assert.match(page, /均值回归/);
  assert.match(page, /因子排名/);
  assert.match(page, /ETF\/50 量化评分/);
});


test("strategy lab has explicit timeout retry and non-advice copy", async () => {
  const page = await source("pages/StrategyLab.tsx");

  assert.match(page, /加载超时/);
  assert.match(page, /重试/);
  assert.match(page, /客观计算/);
  assert.match(page, /不构成投资建议/);
  assert.match(page, /<Disclaimer/);
});
