"use client";

import { Users } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import type { PersonaBreakdown, PersonaOut } from "@/lib/api/types";
import { formatDuration, formatPercent } from "@/lib/utils";

/**
 * Per-agent performance.
 *
 * Deliberately a table, not a chart: each row carries three measures on
 * different scales (a count, a rate, a duration), and the question operators
 * actually ask is "what are Kevin's numbers", which is a lookup. A grouped bar
 * chart here would mean either a dual axis or three charts of three bars.
 *
 * The accent swatch is identity, matching the agent's colour everywhere else in
 * the product — it is never the only carrier of meaning, since the name sits
 * beside it.
 */
export function PersonaBreakdownTable({
  rows,
  personas,
}: {
  rows: PersonaBreakdown[];
  personas: PersonaOut[];
}) {
  const nameFor = (id: string) =>
    personas.find((persona) => persona.id === id)?.display_name ?? id;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Performance by agent</CardTitle>
      </CardHeader>
      <CardBody className="px-0 py-0">
        {rows.length === 0 ? (
          <EmptyState icon={Users} title="No turns recorded yet" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">
                Call count, success rate, and average latency for each agent.
              </caption>
              <thead>
                <tr className="border-b border-hairline text-left">
                  <th scope="col" className="px-5 py-2.5 text-xs font-medium tracking-wide text-ink-muted uppercase">
                    Agent
                  </th>
                  <th scope="col" className="px-5 py-2.5 text-right text-xs font-medium tracking-wide text-ink-muted uppercase">
                    Turns
                  </th>
                  <th scope="col" className="px-5 py-2.5 text-right text-xs font-medium tracking-wide text-ink-muted uppercase">
                    Success
                  </th>
                  <th scope="col" className="px-5 py-2.5 text-right text-xs font-medium tracking-wide text-ink-muted uppercase">
                    Avg latency
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.persona}
                    data-agent={row.persona}
                    className="border-b border-hairline last:border-b-0"
                  >
                    <th scope="row" className="px-5 py-3 text-left font-medium text-ink">
                      <span className="flex items-center gap-2.5">
                        <span aria-hidden className="size-2.5 shrink-0 rounded-full bg-agent" />
                        {nameFor(row.persona)}
                      </span>
                    </th>
                    <td className="tabular px-5 py-3 text-right text-ink-secondary">
                      {row.call_count}
                    </td>
                    <td className="px-5 py-3 text-right">
                      <Badge
                        tone={
                          row.success_rate >= 0.95
                            ? "good"
                            : row.success_rate >= 0.8
                              ? "warning"
                              : "critical"
                        }
                      >
                        {formatPercent(row.success_rate)}
                      </Badge>
                    </td>
                    <td className="tabular px-5 py-3 text-right text-ink-secondary">
                      {formatDuration(row.avg_total_ms)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
