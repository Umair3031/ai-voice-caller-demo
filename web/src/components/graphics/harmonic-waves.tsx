/** Builds a seamless sine-like path using SVG's smooth-quadratic (T) command
 *  — a Q control point, then repeated T's each mirror the last, which is the
 *  standard trick for an evenly-periodic wave with no hand-authored points. */
function wavePath(width: number, height: number, periods: number): string {
  const mid = height / 2;
  const step = width / periods / 2;
  let d = `M0,${mid} Q${step / 2},0 ${step},${mid}`;
  for (let i = 1; i < periods * 2; i += 1) {
    const x = step * (i + 1);
    d += ` T${x},${mid}`;
  }
  return d;
}

const LAYERS = [
  { height: 64, periods: 5, y: 8, duration: 26, opacity: 0.5, strokeWidth: 1.5 },
  { height: 64, periods: 4, y: 34, duration: 34, opacity: 0.32, strokeWidth: 1.5 },
  { height: 64, periods: 6, y: 58, duration: 20, opacity: 0.2, strokeWidth: 1.25 },
] as const;

/**
 * Layered sine lines drifting sideways behind the hero — the "harmonic
 * waves" half of the brief. Each SVG tile is drawn twice, back to back,
 * inside a track twice its width; animating that track from 0 to -50% loops
 * forever with no visible seam, since the second copy is pixel-identical to
 * the first.
 */
export function HarmonicWaves({ color = "var(--brand)" }: { color?: string }) {
  const tileWidth = 600;

  return (
    <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 h-40 overflow-hidden">
      {LAYERS.map((layer, index) => {
        const path = wavePath(tileWidth, layer.height, layer.periods);
        return (
          <div
            key={index}
            className="absolute inset-x-0"
            style={{ top: layer.y, opacity: layer.opacity }}
          >
            <div
              className="flex w-[200%]"
              style={{ animation: `wave-drift ${layer.duration}s linear infinite` }}
            >
              {[0, 1].map((copy) => (
                <svg
                  key={copy}
                  viewBox={`0 0 ${tileWidth} ${layer.height}`}
                  width={tileWidth}
                  height={layer.height}
                  className="block shrink-0"
                  style={{ width: "50%" }}
                  preserveAspectRatio="none"
                >
                  <path
                    d={path}
                    fill="none"
                    stroke={color}
                    strokeWidth={layer.strokeWidth}
                    strokeLinecap="round"
                  />
                </svg>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
