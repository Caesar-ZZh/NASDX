// 通用 echarts 封装：只负责「实例生命周期 + 自适应 + 选项更新」，
// 配色与具体图表形态由调用方在 option 里决定（option 已读取主题色，随 dark/light 重建）。
import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";

interface Props {
  option: EChartsOption;
  className?: string;
  height?: number;
}

export function EChart({ option, className, height = 280 }: Props) {
  const elRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  // 初始化 / 销毁：只跑一次。
  useEffect(() => {
    if (!elRef.current) return;
    const chart = echarts.init(elRef.current, undefined, { renderer: "canvas" });
    chartRef.current = chart;
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(elRef.current);
    return () => {
      ro.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  // option 变化（数据刷新 / 主题切换）即重绘，notMerge 保证彻底替换。
  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: true });
  }, [option]);

  return <div ref={elRef} className={className} style={{ width: "100%", height }} />;
}
