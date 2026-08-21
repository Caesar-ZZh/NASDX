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
  error: string | null;
  refresh: () => void;
}

export function useMarketPulse(enabled: boolean): MarketPulseState {
  const [indices, setIndices] = useState<IndexQuote[] | null>(null);
  const [overview, setOverview] = useState<MarketOverview | null>(null);
  const [industry, setIndustry] = useState<IndustryData | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [polling, setPolling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const failuresRef = useRef(0);
  const inFlightRef = useRef(false);
  const fetchRef = useRef<(() => Promise<boolean>) | null>(null);

  const fetchOnce = useCallback(async (): Promise<boolean> => {
    if (inFlightRef.current) return true;
    inFlightRef.current = true;
    try {
      const [idx, ov, ind] = await Promise.all([
        api.indices(),
        api.marketOverview(),
        api.industry(30),
      ]);
      setIndices(idx);
      setOverview(ov);
      setIndustry(ind);
      setUpdatedAt(Date.now());
      setError(null);
      failuresRef.current = 0;
      return true;
    } catch {
      failuresRef.current += 1;
      if (failuresRef.current >= 2) setError("市场数据获取失败，正在重试…");
      return false;
    } finally {
      inFlightRef.current = false;
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

  return { indices, overview, industry, updatedAt, polling, error, refresh };
}
