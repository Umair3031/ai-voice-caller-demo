"use client";

import * as React from "react";

import { cn } from "@/lib/utils";
import type { TranscriptEntry } from "./use-call";

/**
 * The running conversation.
 *
 * Follows the newest entry by default, but only while the reader is already
 * at the bottom: scroll up to reread something and new turns stop yanking
 * the view away. Scrolling only ever moves this container, never the page.
 */
export function Transcript({
  entries,
  className,
}: {
  entries: TranscriptEntry[];
  className?: string;
}) {
  const scrollRef = React.useRef<HTMLDivElement | null>(null);
  const pinnedRef = React.useRef(true);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };

  React.useEffect(() => {
    const el = scrollRef.current;
    if (!el || !pinnedRef.current) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [entries.length]);

  return (
    <div
      ref={scrollRef}
      onScroll={onScroll}
      className={cn("flex flex-col gap-2.5 overflow-y-auto py-1", className)}
      role="log"
      aria-live="polite"
      aria-label="Call transcript"
    >
      {entries.map((entry) => (
        <Bubble key={entry.id} entry={entry} />
      ))}
    </div>
  );
}

function Bubble({ entry }: { entry: TranscriptEntry }) {
  if (entry.role === "system") {
    return (
      <p className="self-center rounded-full border border-dashed border-hairline px-3 py-1 text-center font-mono text-xs text-ink-muted">
        {entry.text}
      </p>
    );
  }

  const isAgent = entry.role === "agent";
  return (
    <div
      className={cn(
        "max-w-[88%] rounded-[var(--radius-card)] border px-3.5 py-2.5 text-sm",
        isAgent
          ? "self-start rounded-bl-sm border-agent/35 bg-agent-soft text-ink"
          : "self-end rounded-br-sm border-hairline bg-surface-sunken text-ink-secondary",
      )}
    >
      <span className="sr-only">{isAgent ? "Agent said: " : "You said: "}</span>
      {entry.text}
      {entry.degraded ? (
        <span className="mt-1 block text-xs text-ink-muted">Text only — voice unavailable.</span>
      ) : null}
    </div>
  );
}
