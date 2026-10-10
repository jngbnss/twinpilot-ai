import * as echarts from "echarts";
import { useEffect, useRef } from "react";

const BASE: echarts.EChartsOption = {
  backgroundColor: "transparent",
  textStyle: { color: "#8b9aa5", fontFamily: "Pretendard, Segoe UI, sans-serif" },
  grid: { left: 52, right: 20, top: 56, bottom: 30 },
  tooltip: { trigger: "axis", backgroundColor: "#1c252c", borderColor: "#2a3640", textStyle: { color: "#dfe6ea" } },
  legend: { top: 0, type: "scroll", textStyle: { color: "#8b9aa5" } },
};

/** Thin ECharts wrapper: merges a dark base theme and resizes with its container. */
export function Chart({ option, className = "chart" }: { option: echarts.EChartsOption; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    chart.current = echarts.init(ref.current, undefined, { renderer: "canvas" });
    const ro = new ResizeObserver(() => chart.current?.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption({ ...BASE, ...option }, { notMerge: true, lazyUpdate: true });
  }, [option]);

  return <div ref={ref} className={className} />;
}

export const axisStyle = {
  axisLine: { lineStyle: { color: "#2a3640" } },
  splitLine: { lineStyle: { color: "#1f2a31" } },
  axisLabel: { color: "#8b9aa5" },
};
