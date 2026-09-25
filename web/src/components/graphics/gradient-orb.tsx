import type { CSSProperties } from "react";

import { cn } from "@/lib/utils";

/**
 * The product's one recurring graphic: a glossy gradient sphere standing in
 * for "voice" wherever the UI would otherwise reach for a stock mic/person
 * icon. One CSS variable (`--orb-color`) drives the whole sphere — light
 * face, body, and shadowed underside are all `color-mix()` steps off it, so
 * a new persona colour needs no new gradient stops.
 *
 * Deliberately static — "static polish" was the brief. Motion around it
 * comes from `OrbitRings` as a separate layer, not from the sphere itself.
 */
export function GradientOrb({
  color = "var(--brand)",
  size = 96,
  className,
}: {
  color?: string;
  size?: number;
  className?: string;
}) {
  return (
    <div
      className={cn("relative shrink-0 rounded-full", className)}
      style={
        {
          width: size,
          height: size,
          "--orb-color": color,
          background: [
            "radial-gradient(circle at 30% 24%, rgba(255,255,255,0.95), rgba(255,255,255,0) 46%)",
            "radial-gradient(circle at 74% 82%, color-mix(in srgb, var(--orb-color) 55%, black) 0%, transparent 55%)",
            "linear-gradient(150deg, color-mix(in srgb, var(--orb-color) 55%, white) 0%, var(--orb-color) 52%, color-mix(in srgb, var(--orb-color) 70%, black) 100%)",
          ].join(", "),
          boxShadow: `0 ${Math.max(10, size * 0.16)}px ${Math.max(24, size * 0.4)}px -${Math.max(8, size * 0.14)}px color-mix(in srgb, ${color} 55%, transparent)`,
        } as CSSProperties
      }
      aria-hidden
    />
  );
}
