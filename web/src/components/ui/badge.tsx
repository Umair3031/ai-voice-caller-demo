import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
  {
    variants: {
      tone: {
        neutral: "border-hairline bg-surface-sunken text-ink-secondary",
        good: "border-good/30 bg-good-soft text-good",
        warning: "border-warning/30 bg-warning-soft text-warning",
        critical: "border-critical/30 bg-critical-soft text-critical",
        agent: "border-agent/40 bg-agent-soft text-agent",
        brand: "border-brand/30 bg-brand-soft text-brand",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
