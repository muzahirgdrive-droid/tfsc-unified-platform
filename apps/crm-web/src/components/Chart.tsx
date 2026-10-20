/** Thin ECharts wrapper themed with the §22 tokens; resizes with its container. */
import { BarChart, FunnelChart, GaugeChart, HeatmapChart, LineChart, TreemapChart } from 'echarts/charts';
import { AriaComponent, GridComponent, LegendComponent, TooltipComponent, VisualMapComponent } from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';

echarts.use([BarChart, LineChart, GaugeChart, FunnelChart, HeatmapChart, TreemapChart, GridComponent, TooltipComponent, LegendComponent, VisualMapComponent, AriaComponent, CanvasRenderer]);
import { useEffect, useRef } from 'react';

export const PALETTE = ['#2d409d', '#00afe7', '#141956', '#7aa2ff', '#ffb547', '#0f8a4f', '#c4262e'];

export function Chart({ option, height = 260, ariaLabel }: { option: echarts.EChartsCoreOption; height?: number; ariaLabel: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    inst.current = echarts.init(ref.current, undefined, { renderer: 'canvas' });
    const ro = new ResizeObserver(() => inst.current?.resize());
    ro.observe(ref.current);
    return () => { ro.disconnect(); inst.current?.dispose(); };
  }, []);
  useEffect(() => {
    inst.current?.setOption({
      color: PALETTE, textStyle: { fontFamily: 'Outfit, sans-serif', color: '#14214c' }, grid: { left: 40, right: 16, top: 24, bottom: 32, containLabel: true },
      tooltip: { trigger: 'axis', backgroundColor: '#141956', borderWidth: 0, textStyle: { color: '#fff' } }, aria: { enabled: true }, ...option,
    }, true);
  }, [option]);
  return <div ref={ref} role="img" aria-label={ariaLabel} style={{ height, width: '100%' }} />;
}
