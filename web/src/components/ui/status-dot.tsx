import * as React from "react";

import { cn } from "@/lib/utils";

export type StatusTone = "good" | "warning" | "critical" | "neutral";

/**
 * Maps every subscriber-state string the engine can return onto a tone.
 *
 * Centralized so the console's status rows, the health pill, and any future
 * surface agree on what "Degraded" looks like. Unknown values fall back to
 * neutral rather than being silently coloured as healthy.
 */
const STATUS_TONES: Record<string, StatusTone> = {
  Current: "good",
  Online: "good",
  Green: "good",
  "Grace Period": "warning",
  Degraded: "warning",
  Rebooting: "warning",
  "Blinking Red": "warning",
  Overdue: "critical",
  Suspended: "critical",
  Offline: "critical",
  Red: "critical",
  Off: "neutral",
};

export function toneForStatus(value: string): StatusTone {
  return STATUS_TONES[value] ?? "neutral";
}

const TONE_CLASS: Record<StatusTone, string> = {
  good: "bg-good",
  warning: "bg-warning",
  critical: "bg-critical",
  neutral: "bg-neutral",
};

export function StatusDot({
  tone = "neutral",
  pulse = false,
  className,
}: {
  tone?: StatusTone;
  pulse?: boolean;
  className?: string;
}) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block size-2 shrink-0 rounded-full",
        TONE_CLASS[tone],
        pulse && "animate-pulse",
        className,
      )}
    />
  );
}

/** Shared row geometry, so status and plain rows align down the card. */
function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-hairline py-2 text-sm last:border-b-0">
      <span className="text-ink-secondary">{label}</span>
      <span className="flex items-center gap-2 font-medium text-ink">{children}</span>
    </div>
  );
}

/** A value drawn from a known state set — carries a tone dot. */
export function StatusRow({ label, value }: { label: string; value: string }) {
  return (
    <Row label={label}>
      <StatusDot tone={toneForStatus(value)} />
      {value}
    </Row>
  );
}

/**
 * A measured value rather than a state.
 *
 * No dot: a currency figure has no place in the status tone map, so it would
 * always fall through to neutral — a grey dot that means nothing but reads as
 * though it does. `tone` is available where the number genuinely is a signal,
 * such as a negative balance.
 */
export function ValueRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: StatusTone;
}) {
  return (
    <Row label={label}>
      <span className={tone === "critical" ? "text-critical" : undefined}>{value}</span>
    </Row>
  );
}
