"use client";

import { useId } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipContentProps } from "recharts";
import type { MetricPoint } from "@/lib/types";
import { Empty } from "./ui";

const fmtTime = (ts: number) => {
  const d = new Date(ts);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

const compact = (v: number) =>
  Math.abs(v) >= 1e9 ? `${(v / 1e9).toFixed(1)}G` : Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : Math.abs(v) >= 1e3 ? `${(v / 1e3).toFixed(1)}k` : v.toFixed(v % 1 ? 1 : 0);

function ChartTooltip({ active, payload, label, unit }: TooltipContentProps<number, string> & { unit?: string }) {
  if (!active || !payload?.length) return null;
  const v = Number(payload[0].value);
  return (
    <div className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs shadow-md">
      <div className="text-slate-500">{new Date(Number(label)).toLocaleString()}</div>
      <div className="mt-0.5 flex items-center gap-1.5 font-semibold tabular-nums text-slate-900">
        <span className="h-2 w-2 rounded-full" style={{ background: "var(--chart-series-1)" }} />
        {compact(v)}
        {unit}
      </div>
    </div>
  );
}

/** Single-series time chart: 2px line, faint area, recessive grid, crosshair tooltip. */
export function MetricChart({ points, height = 200, unit }: { points: MetricPoint[]; height?: number; unit?: string }) {
  const gradient = useId().replace(/:/g, "");
  const data = points.filter((p) => p.value !== null).map((p) => ({ t: new Date(p.ts).getTime(), v: p.value as number }));
  if (!data.length) return <Empty>Bu davrda ma&apos;lumot yo&apos;q</Empty>;
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--chart-series-1)" stopOpacity={0.18} />
              <stop offset="100%" stopColor="var(--chart-series-1)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={fmtTime}
            stroke="var(--chart-axis)"
            tick={{ fill: "var(--chart-text)" }}
            fontSize={11}
            tickLine={false}
            axisLine={{ stroke: "var(--chart-grid)" }}
            minTickGap={32}
          />
          <YAxis
            tickFormatter={compact}
            tick={{ fill: "var(--chart-text)" }}
            fontSize={11}
            width={44}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip
            cursor={{ stroke: "var(--chart-axis)", strokeDasharray: "3 3" }}
            content={(props) => <ChartTooltip {...(props as TooltipContentProps<number, string>)} unit={unit} />}
          />
          <Area
            type="monotone"
            dataKey="v"
            stroke="var(--chart-series-1)"
            strokeWidth={2}
            fill={`url(#${gradient})`}
            activeDot={{ r: 4, stroke: "var(--chart-surface)", strokeWidth: 2 }}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
