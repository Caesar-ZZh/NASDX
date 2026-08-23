import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";


const source = async (relativePath) =>
  readFile(new URL(`../src/${relativePath}`, import.meta.url), "utf8");


test("API requests abort with a visible timeout error", async () => {
  const api = await source("lib/api.ts");

  assert.match(api, /new AbortController\(\)/);
  assert.match(api, /controller\.abort\(\)/);
  assert.match(api, /请求超时/);
  assert.match(api, /clearTimeout\(/);
  assert.match(api, /marketOverview:.*15_000/);
  assert.match(api, /radarRefresh:.*60_000/);
});

test("market pulse keeps partial successes instead of coupling all panels", async () => {
  const hook = await source("hooks/useMarketPulse.ts");

  assert.match(hook, /Promise\.allSettled\(/);
  assert.doesNotMatch(hook, /Promise\.all\(\[/);
  assert.match(hook, /loading:/);
});

test("daily review and cockpit expose timeout retry states", async () => {
  const daily = await source("pages/DailyReview.tsx");
  const cockpit = await source("pages/Cockpit.tsx");

  assert.match(daily, /加载超时/);
  assert.match(daily, /重试/);
  assert.match(cockpit, /pulse\.error/);
  assert.match(cockpit, /重试/);
});
