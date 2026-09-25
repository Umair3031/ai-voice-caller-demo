import { AlertTriangle, Info, XCircle } from "lucide-react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const alertVariants = cva(
  "flex items-start gap-3 rounded-[var(--radius-card)] border px-4 py-3 text-sm",
  {
    variants: {
      tone: {
        info: "border-brand/25 bg-brand-soft text-ink",
        warning: "border-warning/30 bg-warning-soft text-ink",
        critical: "border-critical/30 bg-critical-soft text-ink",
      },
    },
    defaultVariants: { tone: "info" },
  },
);

const TONE_ICON = { info: Info, warning: AlertTriangle, critical: XCircle } as const;
const TONE_ICON_CLASS = {
  info: "text-brand",
  warning: "text-warning",
  critical: "text-critical",
} as const;

export interface AlertProps extends VariantProps<typeof alertVariants> {
  title?: string;
  children: React.ReactNode;
  className?: string;
}

export function Alert({ tone = "info", title, children, className }: AlertProps) {
  const resolved = tone ?? "info";
  const Icon = TONE_ICON[resolved];
  return (
    <div role="alert" className={cn(alertVariants({ tone }), className)}>
      <Icon className={cn("mt-0.5 size-4 shrink-0", TONE_ICON_CLASS[resolved])} aria-hidden />
      <div className="min-w-0 flex-1">
        {title ? <p className="font-medium text-ink">{title}</p> : null}
        <div className="text-ink-secondary">{children}</div>
      </div>
    </div>
  );
}
