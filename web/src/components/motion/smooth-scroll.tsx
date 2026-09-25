"use client";

import type Lenis from "lenis";
import * as React from "react";

let instance: Lenis | null = null;

/** Glide to an element by id — Lenis owns scrolling, so native smooth
 *  scrolling is switched off while it runs. `start` parks the element `offset`
 *  px below the top (under sticky chrome); `center` puts it mid-screen. */
export function scrollToId(
  id: string,
  { align = "center", offset = 0 }: { align?: "center" | "start"; offset?: number } = {},
): void {
  const el = document.getElementById(id);
  if (!el) return;
  const target = align === "start" ? -offset : -(window.innerHeight - el.offsetHeight) / 2;
  if (instance) {
    instance.scrollTo(el, { offset: target, duration: 1.2 });
  } else if (align === "start") {
    window.scrollTo({
      top: el.getBoundingClientRect().top + window.scrollY - offset,
      behavior: "smooth",
    });
  } else {
    el.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

/**
 * Inertial wheel scrolling for the whole site. Touch keeps the browser's own
 * native momentum, and reduced-motion users get no smoothing at all. Anything
 * marked `data-lenis-prevent` (the call overlay and its transcript) scrolls
 * natively.
 */
export function SmoothScroll() {
  React.useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    let cancelled = false;
    let frame = 0;
    let lenis: Lenis | null = null;

    // Lenis is loaded after first paint, off the critical path.
    const start = async () => {
      const { default: LenisCtor } = await import("lenis");
      if (cancelled) return;
      lenis = new LenisCtor({ lerp: 0.09, wheelMultiplier: 0.9 });
      instance = lenis;
      frame = requestAnimationFrame(function raf(time) {
        lenis?.raf(time);
        frame = requestAnimationFrame(raf);
      });
    };
    const idle = window.requestIdleCallback ?? ((cb: () => void) => window.setTimeout(cb, 300));
    const handle = idle(() => void start());

    return () => {
      cancelled = true;
      if (window.cancelIdleCallback) window.cancelIdleCallback(handle);
      else window.clearTimeout(handle);
      cancelAnimationFrame(frame);
      instance = null;
      lenis?.destroy();
    };
  }, []);

  return null;
}
