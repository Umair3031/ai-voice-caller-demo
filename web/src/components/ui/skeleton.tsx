import { cn } from "@/lib/utils";

/** Loading placeholder. Shaped like the content it stands in for, so layout
 *  does not jump when data lands. */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-surface-sunken", className)} />;
}
