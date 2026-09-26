import { BarChart, CandlestickChart, LineChart } from "echarts/charts";
import { AxisPointerComponent, DataZoomComponent, GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import { buildPanelOption, type PanelInput } from "../lib/panel";
import type { RangeCommand } from "./PriceChart";

echarts.use([CandlestickChart, BarChart, LineChart, GridComponent, TooltipComponent, DataZoomComponent,
             AxisPointerComponent, LegendComponent, CanvasRenderer]);

function windowFor(axis: string[], months: number | null) {
  if (axis.length === 0) return undefined;
  const end = axis[axis.length - 1];
  if (months === null) return { start: axis[0], end };
  const from = new Date(`${end}T00:00:00Z`);
  from.setUTCMonth(from.getUTCMonth() - months);
  const bound = from.toISOString().slice(0, 10);
  return { start: axis.find((d) => d > bound) ?? axis[0], end };
}

/** One panel; panels of one `group` share their zoom and crosshair. */
export function PanelChart({ id, input, group, range, onHover, height = 220 }: {
  id: string;
  input: Omit<PanelInput, "window">;
  group?: string;
  range?: RangeCommand;
  onHover?: (index: number | null) => void;
  height?: number;
}) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const shown = useRef<{ start: string; end: string } | undefined>(undefined);
  const axisRef = useRef<string[]>([]);
  const hover = useRef(onHover);
  hover.current = onHover;

  useEffect(() => {
    const instance = echarts.init(host.current!, undefined, { renderer: "canvas" });
    chart.current = instance;
    window.__stockdc = { charts: { ...(window.__stockdc?.charts ?? {}), [id]: instance } };
    if (group) {
      instance.group = group;
      echarts.connect(group);
    }
    instance.on("datazoom", () => {
      const zoom = (instance.getOption().dataZoom as { startValue?: number; endValue?: number }[] | undefined)?.[0];
      const axis = axisRef.current;
      if (zoom?.startValue !== undefined && zoom.endValue !== undefined) {
        shown.current = { start: axis[zoom.startValue], end: axis[zoom.endValue] };
      }
    });
    instance.on("updateAxisPointer", (event: unknown) => {
      const info = (event as { axesInfo?: { value: number | string }[] }).axesInfo?.[0];
      if (!info || !hover.current) return;
      const index = typeof info.value === "number" ? info.value : axisRef.current.indexOf(info.value);
      if (index >= 0) hover.current(index);
    });
    instance.getZr().on("globalout", () => hover.current?.(null));
    const resize = new ResizeObserver(() => instance.resize());
    resize.observe(host.current!);
    return () => {
      resize.disconnect();
      instance.dispose();
      chart.current = null;
      if (window.__stockdc) delete window.__stockdc.charts[id];
    };
  }, [id, group]);

  useEffect(() => {
    const axis = input.axis;
    const same = axisRef.current.length === axis.length && axisRef.current[0] === axis[0];
    axisRef.current = axis;
    if (!same || !shown.current) shown.current = windowFor(axis, range?.months ?? null);
    chart.current?.setOption(buildPanelOption({ ...input, window: shown.current }), { notMerge: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input]);

  useEffect(() => {
    if (!range) return;
    shown.current = windowFor(axisRef.current, range.months);
    if (shown.current) {
      const axis = axisRef.current;
      chart.current?.dispatchAction({ type: "dataZoom", startValue: axis.indexOf(shown.current.start),
                                      endValue: axis.indexOf(shown.current.end) });
    }
  }, [range]);

  return <div ref={host} className="panel-chart" style={{ height }} data-testid={`chart-${id}`} />;
}
