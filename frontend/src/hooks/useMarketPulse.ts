// 实时市场脉搏：大盘指数 + 涨跌家数 + 行业涨跌幅，盘中轮询聚合。
// 刻意复用 useLiveQuotes 的交易时段判定与退避策略：
// - 5 秒一档（A 股 level-1 快照粒度，拉快无意义）
// - 非交易时段 / 页面切走自动暂停，手动刷新随时可用
// - 连续失败翻倍退避（上限 30s），成功后复位

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { IndexQuote, MarketOverview, IndustryData } from "@/lib/api";
import { isTradingHours } from "@/hooks/useLiveQuotes";

export const PULSE_INTERVAL_MS = 5000;
const MAX_BACKOFF_MS = 30_000;

export interface MarketPulseState {
  indices: IndexQuote[] | null;
  overview: MarketOverview | null;
  industry: IndustryData | null;
  updatedAt: number | null;
  polling: boolean;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useMarketPulse(enabled: boolean): MarketPulseState {
  const [indices, setIndices] = useState<IndexQuote[] | null>(null);
  const [overview, setOverview] = useState<MarketOverview | null>(null);
  const [industry, setIndustry] = useState<IndustryData | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [polling, setPolling] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const failuresRef = useRef(0);
  const inFlightRef = useRef(false);
  const fetchRef = useRef<(() => Promise<boolean>) | null>(null);

  const fetchOnce = useCallback(async (): Promise<boolean> => {
    if (inFlightRef.current) return true;
    inFlightRef.current = true;
    setLoading(true);
    try {
      const [idx, ov, ind] = await Promise.allSettled([
        api.indices(),
        api.marketOverview(),
        api.industry(30),
      ]);
      let successes = 0;
      if (idx.status === "fulfilled") { setIndices(idx.value); successes += 1; }
      if (ov.status === "fulfilled") { setOverview(ov.value); successes += 1; }
      if (ind.status === "fulfilled") { setIndustry(ind.value); successes += 1; }
      if (successes > 0) setUpdatedAt(Date.now());
      if (successes === 3) {
        setError(null);
        failuresRef.current = 0;
        return true;
      }
      failuresRef.current += 1;
      const timedOut = [idx, ov, ind].some(
        (result) => result.status === "rejected" && String(result.reason).includes("超时"),
      );
      setError(timedOut ? "部分市场数据加载超时，可点击重试" : "部分市场数据获取失败，可点击重试");
      return false;
    } finally {
      inFlightRef.current = false;
      setLoading(false);
    }
  }, []);
  fetchRef.current = fetchOnce;

  const refresh = useCallback(() => {
    void fetchOnce();
  }, [fetchOnce]);

  // 首次进入立即拉一次（与开关无关，页面总要有数据）
  useEffect(() => {
    void fetchOnce();
  }, [fetchOnce]);

  // 轮询循环
  useEffect(() => {
    let cancelled = false;
    let timer: number | null = null;
    const clear = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };
    const shouldRun = () => enabled && !document.hidden && isTradingHours();

    const loop = async () => {
      if (cancelled) return;
      if (!shouldRun()) {
        setPolling(false);
        // 没在跑也要保持心跳，好在开盘 / 页面切回来时自动恢复
        timer = window.setTimeout(loop, 10_000);
        return;
      }
      setPolling(true);
      const ok = await fetchOnce();
      if (cancelled) return;
      const wait = ok
        ? PULSE_INTERVAL_MS
        : Math.min(PULSE_INTERVAL_MS * 2 ** failuresRef.current, MAX_BACKOFF_MS);
      timer = window.setTimeout(loop, wait);
    };

    if (enabled) {
      void loop();
    } else {
      setPolling(false);
    }

    const onVisible = () => {
      if (!document.hidden && enabled && !cancelled) {
        clear();
        void loop();
      }
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      cancelled = true;
      clear();
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [enabled, fetchOnce]);

  return { indices, overview, industry, updatedAt, polling, loading, error, refresh };
}
