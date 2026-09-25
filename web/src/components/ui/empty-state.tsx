import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/** Shown wherever a panel has nothing to display yet — never a blank box. */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center gap-2 px-6 py-10 text-center", className)}>
      {Icon ? (
        <div className="mb-1 rounded-full border border-hairline bg-surface-sunken p-2.5">
          <Icon className="size-5 text-ink-muted" aria-hidden />
        </div>
      ) : null}
      <p className="text-sm font-medium text-ink">{title}</p>
      {description ? (
        <p className="max-w-[46ch] text-sm text-ink-muted">{description}</p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
