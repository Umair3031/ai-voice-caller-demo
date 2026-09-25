"use client";

import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { Radio } from "lucide-react";
import * as React from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, Select } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/skeleton";
import { Stat } from "@/components/ui/stat";
import { StatusDot } from "@/components/ui/status-dot";
import { usePersonas } from "@/features/call/use-personas";
import { PersonaBreakdownTable } from "@/features/console/persona-breakdown";
import { SectionNav, type ConsoleSection } from "@/features/console/section-nav";
import {
  SubscriberList,
  SubscriberPanel,
  SubscriberPanelSkeleton,
  useCustomers,
} from "@/features/console/subscriber-panel";
import { ApiError, getTelemetry, queryKeys } from "@/lib/api/client";
import type { PersonaOut, TelemetryEntryOut } from "@/lib/api/types";
import { formatDuration, formatPercent } from "@/lib/utils";

// Recharts is ~100 kB; keep it out of the console's initial bundle.
const chartFallback = () => <Skeleton className="h-64 w-full" />;
const LatencyTrendChart = dynamic(
  () => import("@/features/console/telemetry-charts").then((m) => m.LatencyTrendChart),
  { ssr: false, loading: chartFallback },
);
const StageLatencyChart = dynamic(
  () => import("@/features/console/telemetry-charts").then((m) => m.StageLatencyChart),
  { ssr: false, loading: chartFallback },
);

const SECTIONS: ConsoleSection[] = [
  { id: "overview", label: "Overview" },
  { id: "latency", label: "Latency" },
  { id: "agents", label: "Agents" },
  { id: "subscribers", label: "Subscribers" },
  { id: "turns", label: "Recent turns" },
];

export default function ConsolePage() {
  const [personaFilter, setPersonaFilter] = React.useState<string>("");
  const [selectedPhone, setSelectedPhone] = React.useState<string>("");

  const personasQuery = usePersonas();
  const customersQuery = useCustomers();

  const telemetryQuery = useQuery({
    queryKey: queryKeys.telemetry(personaFilter || undefined),
    queryFn: () => getTelemetry(personaFilter || undefined),
    // Telemetry is a live feed; the rest of the console is not.
    refetchInterval: 10_000,
  });

  // Stable identity: a fresh `?? []` each render would re-run effects on
  // every render.
  const customers = React.useMemo(() => customersQuery.data ?? [], [customersQuery.data]);
  const personas = React.useMemo(() => personasQuery.data ?? [], [personasQuery.data]);
  const entries = React.useMemo(() => telemetryQuery.data?.entries ?? [], [telemetryQuery.data]);
  const selectedCustomer =
    customers.find((customer) => customer.phone_number === selectedPhone) ?? customers[0] ?? null;

  const operatorBlocked =
    customersQuery.error instanceof ApiError &&
    (customersQuery.error.status === 401 || customersQuery.error.status === 503);

  return (
    <div className="space-y-8 pb-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-ink">Operator Console</h1>
            <Badge tone="good">
              <StatusDot tone="good" pulse />
              Live
            </Badge>
          </div>
          <p className="mt-1 max-w-[56ch] text-sm text-ink-secondary">
            Track how the voice agents are performing, and see the customer details they use
            during each call.
          </p>
        </div>

        <Field label="Agent" htmlFor="persona-filter" className="w-48">
          <Select
            id="persona-filter"
            value={personaFilter}
            onChange={(event) => setPersonaFilter(event.target.value)}
          >
            <option value="">All agents</option>
            {personas.map((persona) => (
              <option key={persona.id} value={persona.id}>
                {persona.display_name}
              </option>
            ))}
          </Select>
        </Field>
      </header>

      <SectionNav sections={SECTIONS} />

      {operatorBlocked ? (
        <Alert tone="warning" title="Operator data is locked">
          {customersQuery.error instanceof ApiError
            ? customersQuery.error.message
            : "This deployment has no operator API key configured."}{" "}
          Set <code className="font-mono">OPERATOR_API_KEY</code> in the web app&apos;s
          environment, matching one of the engine&apos;s{" "}
          <code className="font-mono">OPERATOR_API_KEYS</code>.
        </Alert>
      ) : null}

      <section id="overview" aria-label="Overview">
        <SummaryRow summary={telemetryQuery.data?.summary} isPending={telemetryQuery.isPending} />
      </section>

      <section id="latency" aria-label="Latency">
        <div className="grid gap-6 xl:grid-cols-2">
          <LatencyTrendChart entries={entries} />
          <StageLatencyChart stages={telemetryQuery.data?.summary.stage_averages ?? []} />
        </div>
      </section>

      <section id="agents" aria-label="Agents">
        <PersonaBreakdownTable
          rows={telemetryQuery.data?.summary.by_persona ?? []}
          personas={personas}
        />
      </section>

      <section id="subscribers" aria-label="Subscribers" className="space-y-4">
        <h2 className="text-lg font-semibold text-ink">Subscribers</h2>
        {customersQuery.isPending ? (
          <SubscriberPanelSkeleton />
        ) : (
          <div className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
            {customers.length ? (
              <SubscriberList
                customers={customers}
                selectedPhone={selectedCustomer?.phone_number ?? ""}
                onSelect={setSelectedPhone}
              />
            ) : null}
            <SubscriberPanel customer={selectedCustomer} />
          </div>
        )}
      </section>

      <section id="turns" aria-label="Recent turns">
        <RecentTurns entries={entries} personas={personas} />
      </section>
    </div>
  );
}

function SummaryRow({
  summary,
  isPending,
}: {
  summary:
    | { total_calls: number; success_rate: number; avg_total_ms: number; p95_total_ms: number }
    | undefined;
  isPending: boolean;
}) {
  const total = summary?.total_calls ?? 0;
  const cards = [
    { label: "Turns", value: String(total) },
    { label: "Success rate", value: total ? formatPercent(summary?.success_rate ?? 0) : "—" },
    { label: "Avg end-to-end", value: total ? formatDuration(summary?.avg_total_ms ?? 0) : "—" },
    { label: "p95 end-to-end", value: total ? formatDuration(summary?.p95_total_ms ?? 0) : "—" },
  ];

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      {cards.map((card) => (
        <Card key={card.label}>
          <CardBody>
            {isPending ? (
              <div className="space-y-2">
                <Skeleton className="h-3 w-20" />
                <Skeleton className="h-7 w-24" />
              </div>
            ) : (
              <Stat label={card.label} value={card.value} />
            )}
          </CardBody>
        </Card>
      ))}
    </div>
  );
}

/** Newest first — an operator scanning for a problem starts at the top. */
function RecentTurns({
  entries,
  personas,
}: {
  entries: TelemetryEntryOut[];
  personas: PersonaOut[];
}) {
  const newestFirst = React.useMemo(() => [...entries].reverse().slice(0, 8), [entries]);
  const nameFor = (id: string) =>
    personas.find((persona) => persona.id === id)?.display_name ?? id;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent turns</CardTitle>
      </CardHeader>
      <CardBody>
        {newestFirst.length === 0 ? (
          <EmptyState
            icon={Radio}
            title="Nothing yet"
            description="Turns from the live line appear here as they happen."
          />
        ) : (
          <ul className="grid gap-x-8 gap-y-4 lg:grid-cols-2">
            {newestFirst.map((entry) => (
              <li
                key={entry.call_id}
                className="border-b border-hairline pb-4 last:border-b-0 lg:[&:nth-last-child(2):nth-child(odd)]:border-b-0 lg:[&:nth-last-child(2):nth-child(odd)]:pb-0"
              >
                <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                  <Badge
                    tone={
                      entry.outcome === "completed"
                        ? "good"
                        : entry.outcome === "degraded_text_only"
                          ? "warning"
                          : "critical"
                    }
                  >
                    {entry.outcome.replace(/_/g, " ")}
                  </Badge>
                  <Badge tone="neutral">{nameFor(entry.persona)}</Badge>
                  {entry.detected_language ? (
                    <Badge tone="neutral">{entry.detected_language}</Badge>
                  ) : null}
                  <span className="tabular ml-auto font-mono text-xs text-ink-muted">
                    {formatDuration(entry.total_ms)}
                  </span>
                </div>
                <p className="mb-1 font-mono text-xs text-ink-muted">{entry.phone_number}</p>
                {entry.transcript ? (
                  <p className="line-clamp-2 text-sm text-ink-secondary">
                    <span className="text-ink-muted">Caller:</span> {entry.transcript}
                  </p>
                ) : null}
                {entry.response_text ? (
                  <p className="mt-0.5 line-clamp-2 text-sm text-ink">
                    <span className="text-ink-muted">Agent:</span> {entry.response_text}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}
