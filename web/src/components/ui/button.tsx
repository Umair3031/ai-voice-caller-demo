import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * The only button in the system.
 *
 * `agent` renders in whichever persona accent the nearest `data-agent` scope
 * sets, so the live line's call controls pick up Kevin's teal or Jessika's
 * coral without any per-persona component or colour prop.
 */
const buttonVariants = cva(
  cn(
    "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-control)]",
    "text-sm font-medium transition-[background-color,border-color,color,box-shadow,transform]",
    "duration-150 disabled:pointer-events-none disabled:opacity-50",
    "[&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  ),
  {
    variants: {
      variant: {
        primary: "bg-brand text-brand-ink hover:bg-brand-hover shadow-(--shadow-card)",
        agent: "bg-agent text-ink-inverted hover:brightness-110 shadow-(--shadow-card)",
        outline: "border border-strong bg-surface text-ink hover:bg-surface-sunken",
        ghost: "text-ink-secondary hover:bg-surface-sunken hover:text-ink",
        danger: "border border-critical/40 bg-critical-soft text-critical hover:bg-critical/15",
      },
      size: {
        sm: "h-8 px-3 text-xs",
        md: "h-10 px-4",
        lg: "h-11 px-6 text-base",
        icon: "size-10 p-0",
      },
      block: { true: "w-full", false: "" },
    },
    defaultVariants: { variant: "primary", size: "md", block: false },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  /** Render as the child element instead of a `<button>` (e.g. a link). */
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, block, asChild = false, ...props },
  ref,
) {
  const Component = asChild ? Slot : "button";
  return (
    <Component
      ref={ref}
      className={cn(buttonVariants({ variant, size, block }), className)}
      {...props}
    />
  );
});

export { buttonVariants };
