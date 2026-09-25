"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Fades and rises its children into place the first time they scroll into
 * view — the "content arrives as you scroll to it" pattern rather than
 * everything below the fold being fully rendered and static from the first
 * paint. One-shot: once visible, it stays visible (the observer disconnects),
 * so scrolling back up and down doesn't replay it.
 *
 * `delay` (ms) is for staggering a group of these — e.g. a row of cards
 * revealing left to right instead of all at once.
 */
export function Reveal({
  children,
  delay = 0,
  className,
}: {
  children: React.ReactNode;
  delay?: number;
  className?: string;
}) {
  const ref = React.useRef<HTMLDivElement | null>(null);
  const [visible, setVisible] = React.useState(false);

  React.useEffect(() => {
    const el = ref.current;
    if (!el) return;

    // Reduced motion gets the content immediately, no observer at all.
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setVisible(true);
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      // Fires a little before the element's edge reaches the viewport's
      // bottom, so it's finishing rather than starting as it comes level.
      { threshold: 0.1, rootMargin: "0px 0px -10% 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={cn(
        "transition-[opacity,transform] duration-700 ease-out",
        visible ? "translate-y-0 opacity-100" : "translate-y-8 opacity-0",
        className,
      )}
      style={{ transitionDelay: visible ? `${delay}ms` : "0ms" }}
    >
      {children}
    </div>
  );
}
