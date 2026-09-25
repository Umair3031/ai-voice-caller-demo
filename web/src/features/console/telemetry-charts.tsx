"use client";

import * as React from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import type { StageAverage, TelemetryEntryOut } from "@/lib/api/types";
import { stageChartColor } from "@/lib/api/types";
import { formatDuration } from "@/lib/utils";
import { Activity } from "lucide-react";

/**
 * Shared axis/grid styling.
 *
 * Grid and axes are recessive — they orient the eye without competing with the
 * data. Every colour resolves to a design token, so both charts re-theme with
 * the rest of the page and never need a JS theme listener.
 */
const AXIS_TICK = { fill: "var(--ink-muted)", fontSize: 11 };
const GRID_STROKE = "var(--chart-grid)";
const AXIS_STROKE = "var(--chart-axis)";

function TooltipShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-[var(--radius-control)] border border-hairline bg-surface-overlay px-3 py-2 text-xs shadow-(--shadow-overlay)">
      {children}
    </div>
  );
}

// -----------------------------------------------------------------------------
// Average latency by pipeline stage
// -----------------------------------------------------------------------------

/**
 * Horizontal bars: the category labels are words, and horizontal bars give them
 * room to read straight rather than rotated under a vertical axis.
 *
 * Every bar is direct-labelled with its value, which doubles as the secondary
 * encoding the palette's adjacent-pair separation relies on — identity never
 * rests on colour alone.
 */
export function StageLatencyChart({ stages }: { stages: StageAverage[] }) {
  if (!stages.length) {
    return (
      <ChartCard title="Average latency by stage">
        <EmptyState icon={Activity} title="No completed turns yet" />
      </ChartCard>
    );
  }

  const data = stages.map((stage) => ({
    label: stage.label,
    stage: stage.stage,
    avg: stage.avg_ms,
    samples: stage.sample_count,
  }));

  return (
    <ChartCard title="Average latency by stage">
      <ResponsiveContainer width="100%" height={Math.max(180, data.length * 46 + 36)}>
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 4, right: 68, bottom: 4, left: 4 }}
          barCategoryGap={10}
        >
          <CartesianGrid horizontal={false} stroke={GRID_STROKE} />
          <XAxis
            type="number"
            tick={AXIS_TICK}
            stroke={AXIS_STROKE}
            tickLine={false}
            unit=" ms"
          />
          <YAxis
            type="category"
            dataKey="label"
            width={118}
            tick={AXIS_TICK}
            stroke={AXIS_STROKE}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip
            cursor={{ fill: "var(--surface-sunken)" }}
            content={({ active, payload }) => {
              const row = payload?.[0]?.payload as (typeof data)[number] | undefined;
              if (!active || !row) return null;
              return (
                <TooltipShell>
                  <p className="font-medium text-ink">{row.label}</p>
                  <p className="tabular mt-0.5 text-ink-secondary">
                    {formatDuration(row.avg)} average
                  </p>
                  <p className="tabular text-ink-muted">
                    across {row.samples} turn{row.samples === 1 ? "" : "s"}
                  </p>
                </TooltipShell>
              );
            }}
          />
          <Bar
            dataKey="avg"
            // Rounded data-end only; the baseline end stays square so the bar
            // reads as anchored to zero.
            radius={[0, 4, 4, 0]}
            isAnimationActive={false}
            label={{
              position: "right",
              formatter: (value: number) => formatDuration(value),
              fill: "var(--ink-secondary)",
              fontSize: 11,
            }}
          >
            {data.map((row) => (
              // Colour follows the stage, not its rank — filtering the window
              // can never repaint a surviving stage.
              <Cell key={row.stage} fill={stageChartColor(row.stage)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

// -----------------------------------------------------------------------------
// End-to-end latency over recent turns
// -----------------------------------------------------------------------------

/** One series, so no legend box — the title names it. */
export function LatencyTrendChart({ entries }: { entries: TelemetryEntryOut[] }) {
  if (entries.length < 2) {
    return (
      <ChartCard title="End-to-end latency, recent turns">
        <EmptyState
          icon={Activity}
          title="Not enough turns to plot"
          description="Run at least two turns to see the trend."
        />
      </ChartCard>
    );
  }

  const data = entries.map((entry, index) => ({
    index: index + 1,
    total: entry.total_ms,
    persona: entry.persona,
    outcome: entry.outcome,
  }));

  return (
    <ChartCard title="End-to-end latency, recent turns">
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid vertical={false} stroke={GRID_STROKE} />
          <XAxis
            dataKey="index"
            tick={AXIS_TICK}
            stroke={AXIS_STROKE}
            tickLine={false}
            label={{
              value: "Turn sequence",
              position: "insideBottom",
              offset: -2,
              fill: "var(--ink-muted)",
              fontSize: 11,
            }}
          />
          <YAxis
            tick={AXIS_TICK}
            stroke={AXIS_STROKE}
            tickLine={false}
            axisLine={false}
            width={56}
            label={{
              value: "ms",
              position: "insideTopLeft",
              offset: -2,
              fill: "var(--ink-muted)",
              fontSize: 11,
            }}
          />
          <Tooltip
            cursor={{ stroke: AXIS_STROKE, strokeWidth: 1 }}
            content={({ active, payload }) => {
              const row = payload?.[0]?.payload as (typeof data)[number] | undefined;
              if (!active || !row) return null;
              return (
                <TooltipShell>
                  <p className="font-medium text-ink">Turn #{row.index}</p>
                  <p className="tabular mt-0.5 text-ink-secondary">
                    {formatDuration(row.total)} end to end
                  </p>
                  <p className="text-ink-muted">
                    {row.persona} · {row.outcome.replace(/_/g, " ")}
                  </p>
                </TooltipShell>
              );
            }}
          />
          <Line
            type="linear"
            dataKey="total"
            stroke="var(--chart-1)"
            strokeWidth={2}
            // Show each sample while the window is small; past that the dots
            // become noise and the line carries the shape on its own.
            dot={data.length <= 30 ? { r: 2.5, strokeWidth: 0, fill: "var(--chart-1)" } : false}
            // 10px active dot: comfortably bigger than the 2px line it sits on.
            activeDot={{ r: 5, strokeWidth: 2, stroke: "var(--surface)" }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardBody>{children}</CardBody>
    </Card>
  );
}
