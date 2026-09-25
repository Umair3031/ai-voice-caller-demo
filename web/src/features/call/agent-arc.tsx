"use client";

import { Users } from "lucide-react";
import * as React from "react";

import { GradientOrb } from "@/components/graphics/gradient-orb";
import { Reveal } from "@/components/motion/reveal";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import type { PersonaOut } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { usePersonas } from "./use-personas";

const ARC = { cx: 500, cy: 540, r: 440, width: 1000, height: 560 };

// Left, top, right along the upper semicircle, in the order the engine
// returns the roster.
const ANGLES = [152, 90, 28];

function pointAt(angleDeg: number) {
  const rad = (angleDeg * Math.PI) / 180;
  return {
    x: ARC.cx + ARC.r * Math.cos(rad),
    y: ARC.cy - ARC.r * Math.sin(rad),
  };
}

const CLOUD_ALIGN = [
  "left-0 lg:left-1/2 lg:-translate-x-1/2",
  "left-1/2 -translate-x-1/2",
  "right-0 lg:right-auto lg:left-1/2 lg:-translate-x-1/2",
];

/**
 * The agent picker: the roster laid out as three nodes on a semicircle.
 *
 * Rendered entirely from `GET /calls/personas`, so the UI cannot offer an
 * agent the engine would reject, and adding a persona is a backend change
 * with no frontend edit at all. Each node is one button — the orb and its
 * frosted label both start the call — and reports where it sits on screen so
 * the call card can zoom out of it.
 */
export function AgentArc({
  children,
  onSelect,
}: {
  /** Optional: sits in the open space inside the arc (above it on narrow screens). */
  children?: React.ReactNode;
  onSelect: (persona: PersonaOut, origin: { x: number; y: number }) => void;
}) {
  const { data: personas, isPending, isError, error, refetch } = usePersonas();
  const gradientId = React.useId();

  if (isPending) {
    // Same geometry as the loaded arc — a dashed semicircle with a soft teal
    // dot where each agent will sit — so nothing shifts when the roster lands.
    return (
      <div className="relative mx-auto flex max-w-[980px] flex-col items-center gap-6">
        {children ? <div className="w-full max-w-sm">{children}</div> : null}
        <div className="relative w-full pt-28 sm:pt-36">
          <div className="relative aspect-[1000/720] w-full sm:aspect-[1000/560]">
            <svg
              viewBox={`0 0 ${ARC.width} ${ARC.height}`}
              className="absolute inset-0 size-full"
              preserveAspectRatio="none"
              aria-hidden
            >
              <path
                d={`M${ARC.cx - ARC.r},${ARC.cy} A${ARC.r},${ARC.r} 0 0 1 ${ARC.cx + ARC.r},${ARC.cy}`}
                fill="none"
                stroke="var(--brand)"
                strokeOpacity="0.3"
                strokeWidth="2"
                strokeLinecap="round"
                vectorEffect="non-scaling-stroke"
                strokeDasharray="2 10"
              />
            </svg>
            {ANGLES.map((angle) => {
              const { x, y } = pointAt(angle);
              return (
                <div
                  key={angle}
                  aria-hidden
                  className="absolute size-20 -translate-x-1/2 -translate-y-1/2 animate-pulse rounded-full bg-brand/15 ring-1 ring-brand/25"
                  style={{
                    left: `${(x / ARC.width) * 100}%`,
                    top: `${(y / ARC.height) * 100}%`,
                  }}
                />
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  if (isError || !personas?.length) {
    return (
      <div className="mx-auto flex max-w-sm flex-col items-center gap-8">
        {children}
        <Card>
          <CardBody>
            <EmptyState
              icon={Users}
              title="No agents available"
              description={
                error instanceof Error
                  ? error.message
                  : "The engine returned an empty roster."
              }
              action={
                <Button variant="outline" onClick={() => void refetch()}>
                  Retry
                </Button>
              }
            />
          </CardBody>
        </Card>
      </div>
    );
  }

  return (
    <Reveal>
      <div className="relative mx-auto flex max-w-[980px] flex-col items-center gap-6">
        {children ? (
          <div className="w-full max-w-sm md:absolute md:bottom-[5%] md:left-1/2 md:z-10 md:w-80 md:-translate-x-1/2">
            {children}
          </div>
        ) : null}

        <div className="relative w-full pt-28 sm:pt-36">
          <div className="relative aspect-[1000/720] w-full sm:aspect-[1000/560]">
            <svg
              aria-hidden
              viewBox={`0 0 ${ARC.width} ${ARC.height}`}
              className="absolute inset-0 size-full overflow-visible"
              preserveAspectRatio="none"
            >
              <defs>
                <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="0">
                  <stop
                    offset="0%"
                    stopColor="var(--brand)"
                    stopOpacity="0.15"
                  />
                  <stop
                    offset="50%"
                    stopColor="var(--brand)"
                    stopOpacity="0.9"
                  />
                  <stop
                    offset="100%"
                    stopColor="var(--brand)"
                    stopOpacity="0.15"
                  />
                </linearGradient>
              </defs>

              {[
                { r: ARC.r + 64, opacity: 0.35, dash: "2 10" },
                { r: ARC.r - 64, opacity: 0.35, dash: "2 10" },
              ].map(({ r, opacity, dash }) => (
                <path
                  key={r}
                  d={`M${ARC.cx - r},${ARC.cy} A${r},${r} 0 0 1 ${ARC.cx + r},${ARC.cy}`}
                  fill="none"
                  stroke="var(--brand)"
                  strokeOpacity={opacity}
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  vectorEffect="non-scaling-stroke"
                  strokeDasharray={dash}
                />
              ))}

              <path
                d={`M${ARC.cx - ARC.r},${ARC.cy} A${ARC.r},${ARC.r} 0 0 1 ${ARC.cx + ARC.r},${ARC.cy}`}
                fill="none"
                stroke={`url(#${gradientId})`}
                strokeWidth="2"
                strokeLinecap="round"
                vectorEffect="non-scaling-stroke"
              />
              <path
                d={`M${ARC.cx - ARC.r},${ARC.cy} A${ARC.r},${ARC.r} 0 0 1 ${ARC.cx + ARC.r},${ARC.cy}`}
                fill="none"
                stroke="var(--brand)"
                strokeWidth="3"
                strokeLinecap="round"
                vectorEffect="non-scaling-stroke"
                strokeDasharray="70 150"
                style={{ animation: "dash-flow 7s linear infinite" }}
              />
            </svg>

            {personas.slice(0, ANGLES.length).map((persona, index) => {
              const { x, y } = pointAt(ANGLES[index] ?? 90);
              return (
                <AgentNode
                  key={persona.id}
                  persona={persona}
                  index={index}
                  style={{
                    left: `${(x / ARC.width) * 100}%`,
                    top: `${(y / ARC.height) * 100}%`,
                  }}
                  onSelect={onSelect}
                />
              );
            })}
          </div>
        </div>
      </div>
    </Reveal>
  );
}

function AgentNode({
  persona,
  index,
  style,
  onSelect,
}: {
  persona: PersonaOut;
  index: number;
  style: React.CSSProperties;
  onSelect: (persona: PersonaOut, origin: { x: number; y: number }) => void;
}) {
  const orbRef = React.useRef<HTMLDivElement | null>(null);

  return (
    <div
      data-agent={persona.id}
      className="absolute -translate-x-1/2 -translate-y-1/2"
      style={style}
    >
      <button
        type="button"
        onClick={() => {
          const rect = orbRef.current?.getBoundingClientRect();
          onSelect(
            persona,
            rect
              ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
              : { x: window.innerWidth / 2, y: window.innerHeight / 2 },
          );
        }}
        className="group relative block rounded-full focus-visible:outline-offset-8"
        aria-label={`Talk to ${persona.display_name}`}
      >
        <div
          ref={orbRef}
          className="relative size-16 transition-transform duration-300 ease-out group-hover:scale-110 sm:size-24"
        >
          <span
            aria-hidden
            className="absolute inset-0 rounded-full border border-agent/40"
            style={{
              animation: `ring-out 3.6s ease-out ${index * 1.2}s infinite`,
            }}
          />
          <GradientOrb
            color="var(--agent-accent)"
            size={96}
            className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 scale-[0.667] sm:scale-100"
          />
          <span
            aria-hidden
            className="absolute inset-0 flex items-center justify-center text-lg font-bold text-white [text-shadow:0_1px_3px_rgb(0_0_0_/_0.35)] sm:text-2xl"
          >
            {persona.initial}
          </span>
        </div>

        <span
          className={cn(
            "absolute bottom-full mb-3 flex w-max max-w-[9.5rem] flex-col items-center gap-0.5 rounded-[1.4rem] px-3.5 py-2.5 text-center sm:mb-4 sm:max-w-[13rem] sm:px-5 sm:py-3",
            "border border-white bg-white/75 backdrop-blur-md",
            "shadow-[0_0_0_1px_rgba(var(--agent-accent-rgb),0.14),0_18px_40px_-16px_rgba(var(--agent-accent-rgb),0.45)]",
            "transition-shadow duration-300 group-hover:shadow-[0_0_0_1px_rgba(var(--agent-accent-rgb),0.4),0_22px_48px_-14px_rgba(var(--agent-accent-rgb),0.6)]",
            CLOUD_ALIGN[index] ?? CLOUD_ALIGN[1],
          )}
        >
          <span
            aria-hidden
            className="absolute -inset-3 -z-10 rounded-[2rem] bg-agent/10 blur-xl"
          />
          <span className="text-sm font-semibold text-ink sm:text-base">
            {persona.display_name}
          </span>
          <span className="hidden font-mono text-xs text-agent sm:block">
            {persona.role}
          </span>
          <span className="mt-1 rounded-full bg-agent px-3 py-1 text-[0.7rem] font-medium text-white transition-[filter] group-hover:brightness-110 sm:text-xs">
            <span className="sm:hidden">Talk</span>
            <span className="hidden sm:inline">
              Talk to {persona.display_name}
            </span>
          </span>
        </span>
      </button>
    </div>
  );
}
