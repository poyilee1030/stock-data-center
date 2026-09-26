import { BarChart, CandlestickChart, LineChart } from "echarts/charts";
import { AxisPointerComponent, DataZoomComponent, GridComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import { buildChartOption, type ChartInput } from "../lib/chart";

echarts.use([CandlestickChart, BarChart, LineChart, GridComponent, TooltipComponent, DataZoomComponent,
             AxisPointerComponent, MarkLineComponent, CanvasRenderer]);

// The end-to-end check reads each drawn series back from here and compares it
// with the API's answer point by point (scripts/verify_web.sh).
declare global {
  interface Window {
    __stockdc?: { charts: Record<string, echarts.ECharts> };
  }
}

export type RangeCommand = { months: number | null; nonce: number };

type Window_ = { start: string; end: string };

function windowFor(axis: string[], months: number | null): Window_ | undefined {
  if (axis.length === 0) return undefined;
  const end = axis[axis.length - 1];
  if (months === null) return { start: axis[0], end };
  const from = new Date(`${end}T00:00:00Z`);
  from.setUTCMonth(from.getUTCMonth() - months);
  const bound = from.toISOString().slice(0, 10);
  return { start: axis.find((d) => d > bound) ?? axis[0], end };
}

export function PriceChart({ input, range, onHover }: {
  input: Omit<ChartInput, "window">;
  range: RangeCommand;
  onHover: (index: number | null) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const shown = useRef<Window_ | undefined>(undefined);
  const axisRef = useRef<string[]>(input.axis);
  const hover = useRef(onHover);
  hover.current = onHover;

  useEffect(() => {
    const instance = echarts.init(host.current!, undefined, { renderer: "canvas" });
    chart.current = instance;
    window.__stockdc = { charts: { ...(window.__stockdc?.charts ?? {}), price: instance } };
    instance.on("datazoom", () => {
      const zoom = (instance.getOption().dataZoom as { startValue?: number }[] | undefined)?.[0] as
        { startValue?: number; endValue?: number } | undefined;
      const axis = axisRef.current;
      if (zoom?.startValue !== undefined && zoom.endValue !== undefined) {
        shown.current = { start: axis[zoom.startValue], end: axis[zoom.endValue] };
      }
    });
    instance.on("updateAxisPointer", (event: unknown) => {
      const info = (event as { axesInfo?: { value: number | string }[] }).axesInfo?.[0];
      if (!info) return;
      const index = typeof info.value === "number" ? info.value : axisRef.current.indexOf(info.value);
      if (index >= 0) hover.current(index);
    });
    instance.getZr().on("globalout", () => hover.current(null));
    const resize = new ResizeObserver(() => instance.resize());
    resize.observe(host.current!);
    return () => {
      resize.disconnect();
      instance.dispose();
      chart.current = null;
      if (window.__stockdc) delete window.__stockdc.charts.price;
    };
  }, []);

  useEffect(() => {
    const axis = input.axis;
    const sameAxis = axisRef.current.length === axis.length && axisRef.current[0] === axis[0]
      && axisRef.current[axis.length - 1] === axis[axis.length - 1];
    axisRef.current = axis;
    if (!sameAxis || !shown.current) shown.current = windowFor(axis, range.months);
    chart.current?.setOption(buildChartOption({ ...input, window: shown.current }), { notMerge: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input]);

  useEffect(() => {
    shown.current = windowFor(axisRef.current, range.months);
    if (shown.current) {
      const axis = axisRef.current;
      chart.current?.dispatchAction({ type: "dataZoom", startValue: axis.indexOf(shown.current.start),
                                      endValue: axis.indexOf(shown.current.end) });
    }
  }, [range]);

  return <div ref={host} className="chart" data-testid="price-chart" />;
}
