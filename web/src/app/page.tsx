"use client";

import dynamic from "next/dynamic";
import * as React from "react";

import { HarmonicWaves } from "@/components/graphics/harmonic-waves";
import { MicOrbit } from "@/components/graphics/mic-orbit";
import { scrollToId } from "@/components/motion/smooth-scroll";
import { AgentArc } from "@/features/call/agent-arc";
import { useCall } from "@/features/call/use-call";

// Only needed once a call starts.
const loadCallCard = () => import("@/features/call/call-card").then((m) => m.CallCard);
const CallCard = dynamic(loadCallCard);

export default function LiveLinePage() {
  const call = useCall();

  // Fetch the call card while the visitor is still reading, so the first tap
  // on an agent opens it instantly instead of waiting on a download.
  React.useEffect(() => {
    const idle = window.requestIdleCallback ?? ((cb: () => void) => window.setTimeout(cb, 800));
    const handle = idle(() => void loadCallCard());
    return () => {
      if (window.cancelIdleCallback) window.cancelIdleCallback(handle);
      else window.clearTimeout(handle);
    };
  }, []);
  const [origin, setOrigin] = React.useState<{ x: number; y: number } | null>(
    null,
  );

  // Scroll progress through the first screen, as a CSS variable the hero's
  // layers read — the headline eases away slower than the page moves and the
  // mic fades, so the hero dissolves into the content rather than scrolling
  // off as a block.
  const heroRef = React.useRef<HTMLDivElement | null>(null);
  React.useEffect(() => {
    const hero = heroRef.current;
    if (!hero) return;
    let frame = 0;
    const paint = () => {
      frame = 0;
      const p = Math.min(
        1,
        Math.max(0, window.scrollY / (window.innerHeight * 0.85)),
      );
      hero.style.setProperty("--hero-p", p.toFixed(3));
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(paint);
    };
    paint();
    window.addEventListener("scroll", schedule, { passive: true });
    return () => {
      window.removeEventListener("scroll", schedule);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <>
      {/* No sheet, no clipped edge: the hero's bottom is masked to nothing and
          the page-wide ambient flakes take over underneath it, so scrolling
          reads as one continuous surface. */}
      <div
        ref={heroRef}
        className="relative -mt-6 mr-[calc(50%-50vw)] ml-[calc(50%-50vw)] sm:-mt-8"
      >
        <section className="relative flex min-h-[calc(100vh-6rem)] items-center justify-center overflow-x-clip px-4 text-center [mask-image:linear-gradient(to_bottom,#000_72%,transparent_100%)] sm:px-6 lg:h-[max(560px,calc(100vh-4rem))] lg:min-h-0">
          {/* On desktop the mic is pinned to the exact centre of the first
              screen; below lg it falls back into normal flow and scales down
              to fit the width. The headline sits dead centre over it. */}
          <div
            className="pointer-events-none relative flex w-full justify-center py-4 lg:absolute lg:inset-0 lg:items-center lg:py-0"
            style={{ opacity: "calc(1 - var(--hero-p, 0) * 0.9)" }}
          >
            <div className="origin-center scale-[0.7] -my-20 sm:scale-90 sm:-my-8 lg:my-0 lg:scale-100">
              <MicOrbit size={168} />
            </div>
            <h1 className="absolute inset-0 z-[60] flex items-center justify-center px-4 text-4xl leading-[1.05] font-bold text-ink [text-shadow:0_0_24px_rgb(255_255_255_/_0.95),0_0_8px_rgb(255_255_255_/_0.9)] sm:text-5xl lg:text-6xl">
              <span className="relative -top-[57px] sm:-top-[76px] lg:-top-[89px]">
                Pick a specialist.
                <br />
                <span className="text-brand">Start talking.</span>
              </span>
            </h1>
          </div>
        </section>

        <div
          className="pointer-events-none absolute inset-x-0 bottom-6 z-20 flex justify-center"
          style={{ opacity: "calc(1 - var(--hero-p, 0) * 4)" }}
        >
          <button
            type="button"
            onClick={() => scrollToId("agents")}
            className="pointer-events-auto flex flex-col items-center gap-1.5 text-xs font-medium text-ink-secondary opacity-0"
            style={{ animation: "cue-in 0.8s ease-out 1s forwards" }}
          >
            <span>Scroll down to talk with an agent</span>
            <span
              aria-hidden
              className="flex h-9 w-6 items-start justify-center rounded-full border border-brand/50 pt-1.5"
            >
              <span
                className="block h-2 w-1 rounded-full bg-brand"
                style={{ animation: "cue-bob 1.6s ease-in-out infinite" }}
              />
            </span>
          </button>
        </div>

        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-40 [mask-image:linear-gradient(to_bottom,#000_50%,transparent)]">
          <HarmonicWaves />
        </div>
      </div>

      <div className="mx-auto max-w-[1280px] pt-6 pb-24 sm:pt-10">
        <section id="agents" aria-label="Choose a specialist" className="mt-8 sm:mt-16">
          <AgentArc
            onSelect={(persona, at) => {
              setOrigin(at);
              call.startCall(persona);
            }}
          />
        </section>
      </div>

      {call.isActive ? <CallCard call={call} origin={origin} /> : null}
    </>
  );
}
