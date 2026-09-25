"use client";

import { useQuery } from "@tanstack/react-query";
import { UserX } from "lucide-react";
import * as React from "react";

import { Card, CardBody } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusDot, toneForStatus } from "@/components/ui/status-dot";
import { listCustomers, queryKeys } from "@/lib/api/client";
import type { CustomerOut } from "@/lib/api/types";
import { cn, formatCurrency, formatPercent } from "@/lib/utils";

export function useCustomers() {
  return useQuery({ queryKey: queryKeys.customers, queryFn: listCustomers });
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

/** The subscriber's worst current state, as one dot for the list. */
function worstTone(customer: CustomerOut) {
  const tones = [customer.payment_status, customer.router_status, customer.los_light].map(
    toneForStatus,
  );
  if (tones.includes("critical")) return "critical" as const;
  if (tones.includes("warning")) return "warning" as const;
  return "good" as const;
}

/**
 * Every subscriber the engine holds, as a selectable list — replaces a
 * dropdown that truncated the number it was there to show.
 */
export function SubscriberList({
  customers,
  selectedPhone,
  onSelect,
}: {
  customers: CustomerOut[];
  selectedPhone: string;
  onSelect: (phone: string) => void;
}) {
  return (
    <ul
      aria-label="Subscribers"
      className="flex gap-2 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible lg:pb-0"
    >
      {customers.map((customer) => {
        const selected = customer.phone_number === selectedPhone;
        return (
          <li key={customer.phone_number} className="min-w-[15rem] lg:min-w-0">
            <button
              type="button"
              aria-pressed={selected}
              onClick={() => onSelect(customer.phone_number)}
              className={cn(
                "flex w-full items-center gap-3 rounded-[var(--radius-card)] border bg-surface px-3.5 py-3 text-left transition-all",
                selected
                  ? "border-brand shadow-[0_0_0_1px_rgba(var(--brand-rgb),0.3),0_14px_30px_-16px_rgba(var(--brand-rgb),0.5)]"
                  : "border-hairline hover:border-strong hover:shadow-(--shadow-md)",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "flex size-10 shrink-0 items-center justify-center rounded-full text-sm font-semibold",
                  selected ? "bg-brand text-brand-ink" : "bg-brand-soft text-brand",
                )}
              >
                {initials(customer.customer_name)}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-semibold text-ink">
                  {customer.customer_name}
                </span>
                <span className="block truncate font-mono text-xs text-ink-muted">
                  {customer.phone_number}
                </span>
              </span>
              <StatusDot tone={worstTone(customer)} />
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * Live subscriber state for the selected caller — the same record the agent
 * sees injected into its prompt, so an operator can check what the agent is
 * actually grounded in. Read-only: this console reports, it does not edit.
 */
export function SubscriberPanel({ customer }: { customer: CustomerOut | null }) {
  if (!customer) {
    return (
      <Card>
        <CardBody>
          <EmptyState
            icon={UserX}
            title="No subscribers available"
            description="The agent treats an unknown caller as unverified and says so rather than inventing account details."
          />
        </CardBody>
      </Card>
    );
  }

  const owing = customer.account_balance < 0;

  return (
    <Card>
      <CardBody className="space-y-6">
        <div className="flex flex-wrap items-center gap-4">
          <span
            aria-hidden
            className="flex size-14 shrink-0 items-center justify-center rounded-full bg-brand text-lg font-semibold text-brand-ink"
          >
            {initials(customer.customer_name)}
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-xl font-semibold text-ink">{customer.customer_name}</h2>
            <p className="tabular font-mono text-sm text-ink-secondary">{customer.phone_number}</p>
          </div>
          <span className="rounded-full border border-strong px-3 py-1 text-xs font-medium text-ink-secondary">
            {customer.support_tier} tier
          </span>
        </div>

        <dl className="grid gap-x-8 gap-y-5 sm:grid-cols-2">
          <Fact label="Account ID" value={customer.account_id} mono />
          <Fact label="Plan" value={customer.active_plan} />
          <div>
            <dt className="text-xs font-medium tracking-wide text-ink-muted uppercase">
              Account balance
            </dt>
            <dd
              className={cn(
                "tabular mt-1 text-2xl font-semibold",
                owing ? "text-critical" : "text-ink",
              )}
            >
              {formatCurrency(customer.account_balance, customer.currency)}
              {owing ? (
                <span className="ml-2 text-xs font-medium text-ink-muted">outstanding</span>
              ) : null}
            </dd>
          </div>
          <div>
            <dt className="flex items-baseline justify-between text-xs font-medium tracking-wide text-ink-muted uppercase">
              Data used
              <span className="tabular text-sm tracking-normal text-ink normal-case">
                {formatPercent(customer.data_usage_ratio)}
              </span>
            </dt>
            <dd className="mt-2">
              <UsageBar ratio={customer.data_usage_ratio} />
              <p className="tabular mt-1.5 text-xs text-ink-muted">
                {customer.plan_data_used_gb.toFixed(1)} of {customer.plan_data_cap_gb.toFixed(0)} GB
              </p>
            </dd>
          </div>
        </dl>

        <div className="grid gap-3 sm:grid-cols-3">
          <StateTile label="Payment" value={customer.payment_status} />
          <StateTile label="Router" value={customer.router_status} />
          <StateTile label="LOS light" value={customer.los_light} />
        </div>
      </CardBody>
    </Card>
  );
}

function Fact({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium tracking-wide text-ink-muted uppercase">{label}</dt>
      <dd
        className={cn("mt-1 truncate text-base font-medium text-ink", mono && "font-mono text-sm")}
      >
        {value}
      </dd>
    </div>
  );
}

function StateTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[var(--radius-card)] bg-surface-sunken px-4 py-3">
      <p className="text-xs font-medium tracking-wide text-ink-muted uppercase">{label}</p>
      <p className="mt-1 flex items-center gap-2 text-sm font-semibold text-ink">
        <StatusDot tone={toneForStatus(value)} />
        {value}
      </p>
    </div>
  );
}

/** Tone shifts as the cap approaches — the bar says "nearly out", not just "80%". */
function UsageBar({ ratio }: { ratio: number }) {
  const tone = ratio >= 0.95 ? "bg-critical" : ratio >= 0.8 ? "bg-warning" : "bg-good";
  return (
    <div
      className="h-2 w-full overflow-hidden rounded-full bg-surface-sunken"
      role="progressbar"
      aria-valuenow={Math.round(ratio * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label="Data allowance used"
    >
      <div
        className={`h-full rounded-full transition-[width] duration-300 ${tone}`}
        style={{ width: `${Math.min(100, Math.max(ratio * 100, 1.5))}%` }}
      />
    </div>
  );
}

export function SubscriberPanelSkeleton() {
  return (
    <Card>
      <CardBody className="space-y-4">
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-16 w-full" />
      </CardBody>
    </Card>
  );
}
