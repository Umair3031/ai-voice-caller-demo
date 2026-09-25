import { cn } from "@/lib/utils";

/**
 * A single headline number.
 *
 * Deliberately plain: no sparkline, no coloured delta chrome. The value is the
 * message, and `tabular` keeps the digits from jittering as it updates in place.
 */
export function Stat({
  label,
  value,
  hint,
  className,
}: {
  label: string;
  value: string;
  hint?: string;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <p className="text-xs font-medium tracking-wide text-ink-muted uppercase">{label}</p>
      <p className="tabular mt-1 truncate text-2xl font-semibold text-ink">{value}</p>
      {hint ? <p className="mt-0.5 truncate text-xs text-ink-muted">{hint}</p> : null}
    </div>
  );
}
