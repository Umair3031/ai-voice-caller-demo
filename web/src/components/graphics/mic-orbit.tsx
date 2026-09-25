"use client";

import * as React from "react";

import {
  FLAKE_HEIGHT_RATIO,
  FLAKE_PATHS,
  SPRITE_PAD,
  bucketOf,
  spriteFor,
} from "./flake-sprites";

/** Mixed widths — integers, indexed by position, so the markup is
 *  deterministic (no Math.random). */
const WIDTHS = [46, 58, 70, 82, 96, 54, 76, 64] as const;


/** Height multipliers, so some chips are rounder and some longer. */
const ASPECTS = [1, 0.85, 1.12, 0.95, 1.2, 0.9] as const;

/* Drawn from independent low-discrepancy sequences, never from index parity:
 * the position sequence ties parity to the top or bottom half of the screen,
 * so anything picked by parity would end up bunched into one half. */
function variantOf(index: number): number {
  return Math.floor(radical(index + 1, 5) * FLAKE_PATHS.length);
}
function aspectOf(index: number): number {
  return ASPECTS[Math.floor(radical(index + 1, 11) * ASPECTS.length)]!;
}

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

/** Hard ceiling on flakes drawn. */
const MAX_FLAKES = 360;

type Layout = {
  /** Half-extent, in the field's own px, the canvases must cover. */
  reachX: number;
  reachY: number;
  widths: number[];
  /** Each flake's fixed spot — the even Halton scatter, and the centre it
   *  loops around. Never changes once laid out, which is what keeps the
   *  overall coverage identical for as long as the page stays open. */
  xHome: number[];
  yHome: number[];
  /** Half-width of the small loop a flake makes around its own home spot. */
  orbitRx: number[];
  /** Half-height of that same loop (the orbit's tilt). */
  orbitRy: number[];
  phase: number[];
  /** Angular speed, rad/s. */
  omega: number[];
  /** Amplitude, px, of the small wind-like vibration each flake keeps. */
  vib: number[];
  /** Degrees: the direction, in each flake's own field of view, that the mic's
   *  light comes from — used to fade the dot texture toward that side. */
  lightAngle: number[];
};

/** Low-discrepancy sequence in (0, 1): successive values fill the range
 *  evenly instead of clumping, which a plain counter or Math.random wouldn't.
 *  Two different bases together (a Halton set) fill a 2-D area evenly with no
 *  streaks or bare patches. */
function radical(n: number, base: number): number {
  let f = 1;
  let r = 0;
  while (n > 0) {
    f /= base;
    r += f * (n % base);
    n = Math.floor(n / base);
  }
  return r;
}

/**
 * Scatters flakes over the whole window. Each flake's home spot is one point
 * of a Halton set spanning the full width and height, so coverage is even
 * top to bottom and side to side — and it stays that way forever, because a
 * flake only ever loops in a small circle around its own home; it never
 * travels far enough to crowd a neighbour or leave a gap where it started.
 * The count follows the window's area, so density stays about the same from
 * a phone to an ultrawide monitor.
 *
 * Movement depends on how far a flake's home is from the mic: the nearest
 * loop round it at a visible size and speed — a real, small revolution — and
 * that fades smoothly with distance until the outer 22% hold their spot and
 * only tremble a little, like leaves in a light wind.
 */
function buildLayout(size: number, halfWidth: number, halfHeight: number): Layout {
  const count = clamp(Math.round((halfWidth * 2 * halfHeight * 2) / 4400), 60, MAX_FLAKES);
  const R = Math.max(size * 1.4, halfWidth * 1.02);
  const dMin = size * 1.05;
  const dMax = Math.hypot(R, halfHeight);

  const layout: Layout = {
    reachX: R,
    reachY: halfHeight,
    widths: [],
    xHome: [],
    yHome: [],
    orbitRx: [],
    orbitRy: [],
    phase: [],
    omega: [],
    vib: [],
    lightAngle: [],
  };
  for (let i = 0; i < count; i += 1) {
    const x = (radical(i + 1, 3) * 2 - 1) * R;
    const yHome = (radical(i + 1, 2) * 2 - 1) * halfHeight;
    const t = clamp((Math.hypot(x, yHome) - dMin) / Math.max(1, dMax - dMin) / 0.78, 0, 1);

    // The loop's own size, capped small and shrinking with distance — this
    // is what keeps "revolves around the mic" true only near the mic, and
    // is why this can never turn into the wide sweep the scatter needs
    // protecting from.
    const orbitRx = size * 0.85 * Math.pow(1 - t, 1.6);

    layout.widths.push(WIDTHS[Math.floor(radical(i + 1, 7) * WIDTHS.length)]!);
    layout.xHome.push(x);
    layout.yHome.push(yHome);
    layout.orbitRx.push(orbitRx);
    layout.orbitRy.push(orbitRx * 0.55);
    layout.phase.push(radical(i + 1, 13) * Math.PI * 2);
    // Very slow: the flakes nearest the mic loop at 0.12 rad/s (a lap in
    // ~52s), fading smoothly to none at all in the outer 22%.
    layout.omega.push(0.12 * Math.pow(1 - t, 1.6));
    // Everyone keeps a faint sideways-and-up-and-down tremble, like leaves on
    // water in a light wind; the farther out, the more of their movement
    // it is (they have nothing else, once their loop shrinks to nothing).
    layout.vib.push(1.5 + 3.5 * t);
    // The mic sits at this whole field's origin (0, 0), so the direction a
    // flake's own light comes from is simply the bearing back to the origin
    // from its home spot — computed once here rather than from the live,
    // looping position, which barely matters now that the loop is small
    // enough to never carry a flake far from that home.
    layout.lightAngle.push((Math.atan2(-yHome, -x) * 180) / Math.PI);
  }
  return layout;
}

/** Pure white, filled solid, with only a softly glowing silver-gold edge. */
function MicGlyph({ height }: { height: number }) {
  const edge = "#cfd2db";
  return (
    <svg
      viewBox="0 0 200 244"
      height={height}
      width={(height * 200) / 244}
      aria-hidden
      style={{
        overflow: "visible",
        filter:
          "drop-shadow(0 0 5px rgba(214, 200, 150, 0.7)) drop-shadow(0 0 16px rgba(214, 200, 150, 0.45))",
      }}
    >
      <path
        d="M34,92 V112 C34,152 64,178 100,178 C136,178 166,152 166,112 V92"
        fill="none"
        stroke={edge}
        strokeWidth="15"
        strokeLinecap="round"
      />
      <path
        d="M34,92 V112 C34,152 64,178 100,178 C136,178 166,152 166,112 V92"
        fill="none"
        stroke="#ffffff"
        strokeWidth="11"
        strokeLinecap="round"
      />
      <rect x="93" y="176" width="14" height="44" rx="7" fill="#ffffff" stroke={edge} strokeWidth="2.5" />
      <rect x="56" y="216" width="88" height="15" rx="7.5" fill="#ffffff" stroke={edge} strokeWidth="2.5" />
      <rect x="62" y="8" width="76" height="138" rx="38" fill="#ffffff" stroke={edge} strokeWidth="3" />
    </svg>
  );
}

/**
 * The hero's centrepiece: a still white mic with a softly glowing border,
 * carpeted with silk flakes scattered evenly over the whole window, top to
 * bottom and side to side — the exact spread the page first paints with,
 * held for as long as the page stays open, since no flake ever travels far
 * from the home spot it was scattered to.
 *
 * Each flake instead loops in a small circle around that home spot, seen
 * from slightly above so the loop tilts into an ellipse: the near side
 * (bigger, brighter, passing in front of the mic) travels left to right,
 * the far side returns behind it. Only flakes whose home is close to the
 * mic have a loop large enough to actually revolve round it; farther out
 * the loop shrinks to nothing and a flake just holds its spot with a faint
 * tremble, like a leaf in a light wind. The mic itself never moves.
 *
 * Each flake also has a fixed bearing back to the mic, computed once from
 * that same home spot, which the dot texture on its face fades toward —
 * the side facing the mic reads brighter, the far side dimmer, as if lit
 * from that one source. Since home spots are scattered all around the mic,
 * no two nearby flakes are lit from the same side by coincidence alone.
 *
 * The cursor only deflects: flakes within its reach are pushed radially out
 * of the way, like water parting round a hull, and settle back on a damped
 * spring toward their loop. It never moves, tilts or speeds up anything.
 */
export function MicOrbit({ size = 104 }: { size?: number }) {
  const wrapRef = React.useRef<HTMLDivElement | null>(null);
  const backRef = React.useRef<HTMLCanvasElement | null>(null);
  const frontRef = React.useRef<HTMLCanvasElement | null>(null);
  const [layout, setLayout] = React.useState<Layout | null>(null);

  const span = size * 2.9;
  const micHeight = size * 2.1;

  // Canvas margin around the scatter, for a flake's own size, its loop and
  // the cursor's push.
  const MARGIN = 110;
  const fieldW = layout ? (layout.reachX + MARGIN) * 2 : 0;
  const fieldH = layout ? (layout.reachY + MARGIN) * 2 : 0;

  // Rebuild the field whenever the window (and so the available width) changes.
  React.useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;

    let timer: ReturnType<typeof setTimeout> | undefined;

    function measure() {
      const rect = wrap!.getBoundingClientRect();
      // The wrapper may be scaled down by an ancestor on small screens;
      // work in its own (unscaled) pixels so layout stays consistent.
      const scale = rect.width / span || 1;
      // The hero is roughly a viewport tall less the header and padding.
      setLayout(
        buildLayout(size, window.innerWidth / 2 / scale, (window.innerHeight - 96) / 2 / scale),
      );
    }

    measure();
    function onResize() {
      clearTimeout(timer);
      timer = setTimeout(measure, 150);
    }
    window.addEventListener("resize", onResize);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("resize", onResize);
    };
  }, [size, span]);

  React.useEffect(() => {
    const wrap = wrapRef.current;
    const back = backRef.current;
    const front = frontRef.current;
    if (!wrap || !back || !front || !layout) return;
    const { xHome, yHome, orbitRx, orbitRy, phase, omega, vib, lightAngle, widths } = layout;
    const count = widths.length;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // Backing-store pixels per field pixel: the wrapper's own scale (it may be
    // shrunk by an ancestor) times the device ratio, capped so a hi-dpi screen
    // doesn't get a huge canvas for soft, blurry chips.
    const rect0 = wrap.getBoundingClientRect();
    const ratio = (rect0.width / span || 1) * Math.min(window.devicePixelRatio || 1, 1.5);
    for (const canvas of [back, front]) {
      canvas.width = Math.max(1, Math.round(fieldW * ratio));
      canvas.height = Math.max(1, Math.round(fieldH * ratio));
    }
    const backCtx = back.getContext("2d")!;
    const frontCtx = front.getContext("2d")!;

    // Everything about a flake that never changes frame to frame.
    const sprites: HTMLCanvasElement[] = [];
    const drawW: number[] = [];
    const drawH: number[] = [];
    const flakeScale: number[] = [];
    for (let index = 0; index < count; index += 1) {
      const width = widths[index]!;
      const height = width * FLAKE_HEIGHT_RATIO * aspectOf(index);
      sprites.push(spriteFor(variantOf(index), bucketOf(lightAngle[index]!)));
      drawW.push(width * (1 + (2 * SPRITE_PAD) / 100));
      drawH.push(height * (1 + (2 * SPRITE_PAD) / 60));
      flakeScale.push(0.85 + radical(index + 1, 17) * 0.3);
    }

    // The pointer in the field's own pixels. It only ever deflects flakes
    // near it; the stream itself never leans, shifts or speeds up.
    const cursor = { x: 0, y: 0, active: false };
    let clock = 0;

    // Per-flake displacement from its orbit position, and its velocity.
    const ox = new Float32Array(count);
    const oy = new Float32Array(count);
    const ovx = new Float32Array(count);
    const ovy = new Float32Array(count);

    const RADIUS = size * 0.85; // how far the "hull" disturbs the water
    const PUSH = 3600; // outward acceleration at the cursor, px/s²
    const SPRING = 12; // pull back to the orbit
    const DAMPING = 4.2; // water resistance

    function onMove(event: PointerEvent) {
      const rect = wrap!.getBoundingClientRect();
      const scale = rect.width / span || 1;
      cursor.x = (event.clientX - (rect.left + rect.width / 2)) / scale;
      cursor.y = (event.clientY - (rect.top + rect.height / 2)) / scale;
      cursor.active = true;
    }

    function onLeave() {
      cursor.active = false;
    }

    function place(dt: number) {
      const damp = Math.exp(-DAMPING * dt);
      backCtx.setTransform(1, 0, 0, 1, 0, 0);
      backCtx.clearRect(0, 0, back!.width, back!.height);
      frontCtx.setTransform(1, 0, 0, 1, 0, 0);
      frontCtx.clearRect(0, 0, front!.width, front!.height);
      const cx = (fieldW / 2) * ratio;
      const cy = (fieldH / 2) * ratio;

      for (let index = 0; index < count; index += 1) {
        const angle = phase[index]! + omega[index]! * clock;
        const rxi = orbitRx[index]!;
        const ryi = orbitRy[index]!;

        // Front-to-back depth in [-1, 1]; positive is nearer the viewer.
        const depth = Math.sin(angle);
        // The tremble: two slow, unrelated sine waves per flake.
        const wx = Math.sin(clock * (0.55 + ((index * 7) % 10) * 0.06) + index * 2.1) * vib[index]!;
        const wy =
          Math.sin(clock * (0.43 + ((index * 5) % 10) * 0.05) + index * 3.3) * vib[index]! * 0.6;
        // A small loop centred on the flake's own home spot — negated cosine
        // so the near side (depth > 0) travels left to right.
        const px = xHome[index]! - Math.cos(angle) * rxi + wx;
        const py = yHome[index]! + depth * ryi + wy;

        if (dt > 0) {
          let ax = -SPRING * ox[index]!;
          let ay = -SPRING * oy[index]!;

          if (cursor.active) {
            const fx = px + ox[index]! - cursor.x;
            const fy = py + oy[index]! - cursor.y;
            const distance = Math.hypot(fx, fy);
            if (distance < RADIUS) {
              const s = 1 - distance / RADIUS;
              const push = s * s * PUSH;
              const norm = distance || 1;
              ax += (fx / norm) * push;
              ay += (fy / norm) * push;
            }
          }

          ovx[index] = (ovx[index]! + ax * dt) * damp;
          ovy[index] = (ovy[index]! + ay * dt) * damp;
          ox[index] = clamp(ox[index]! + ovx[index]! * dt, -260, 260);
          oy[index] = clamp(oy[index]! + ovy[index]! * dt, -260, 260);
        }

        const near = (depth + 1) / 2;

        // Each chip slowly turns in 3D on its own cycle, so its outline
        // foreshortens and changes shape rather than staying a flat decal.
        // On a canvas that's a squash along each axis by the cosine of the
        // turn — the same effect the perspective transform gave.
        const yaw = Math.sin(clock * (0.32 + ((index * 17) % 10) * 0.025) + index * 1.7) * 62;
        const pitch = Math.sin(clock * (0.27 + ((index * 11) % 10) * 0.02) + index * 2.3) * 32;
        const squashX = Math.cos((yaw * Math.PI) / 180);
        const squashY = Math.cos((pitch * Math.PI) / 180);

        // Follow the path's direction, softened so ends don't spin upright;
        // a scattered flake also tumbles with the push it received.
        const vx = Math.sin(angle) * rxi;
        const vy = Math.cos(angle) * ryi;
        const rotation =
          (((Math.atan2(vy, vx) * 180) / Math.PI) * 0.55 +
            Math.sin(angle * 3 + index) * 9 +
            clamp(ovx[index]! * 0.07, -55, 55)) *
          (Math.PI / 180);

        const s = flakeScale[index]!;
        const cos = Math.cos(rotation);
        const sin = Math.sin(rotation);
        const sx = s * squashX * ratio;
        const sy = s * squashY * ratio;

        // Behind the mic until a flake swings round to its near side, then in
        // front of it: two canvases either side of the mic, split at depth 0.
        const g = depth > 0 ? frontCtx : backCtx;
        g.globalAlpha = 0.9 + 0.08 * near;
        g.setTransform(
          cos * sx,
          sin * sx,
          -sin * sy,
          cos * sy,
          cx + (px + ox[index]!) * ratio,
          cy + (py + oy[index]!) * ratio,
        );
        g.drawImage(
          sprites[index]!,
          -drawW[index]! / 2,
          -drawH[index]! / 2,
          drawW[index]!,
          drawH[index]!,
        );
      }
    }

    place(0);
    if (reduced) return;

    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerleave", onLeave);
    document.addEventListener("pointerleave", onLeave);

    let last = performance.now();
    let frame = 0;

    function tick(now: number) {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;

      // Nothing to see once the hero has scrolled away or the tab is hidden,
      // so skip the physics and the redraw for those frames.
      const offscreen = document.hidden || window.scrollY > window.innerHeight * 1.1;
      if (!offscreen) {
        clock += dt;
        place(dt);
      }
      frame = requestAnimationFrame(tick);
    }

    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerleave", onLeave);
      document.removeEventListener("pointerleave", onLeave);
    };
  }, [layout, size, span, fieldW, fieldH]);

  const canvasStyle: React.CSSProperties = {
    width: fieldW,
    height: fieldH,
    marginLeft: -fieldW / 2,
    marginTop: -fieldH / 2,
  };

  return (
    <div ref={wrapRef} className="relative shrink-0" style={{ width: span, height: span }}>
      <canvas
        ref={backRef}
        aria-hidden
        className="pointer-events-none absolute top-1/2 left-1/2 z-[5]"
        style={canvasStyle}
      />

      <div
        className="absolute top-1/2 left-1/2 z-10"
        style={{
          marginLeft: -((micHeight * 200) / 244) / 2,
          marginTop: -micHeight / 2,
        }}
      >
        <MicGlyph height={micHeight} />
      </div>

      <canvas
        ref={frontRef}
        aria-hidden
        className="pointer-events-none absolute top-1/2 left-1/2 z-20"
        style={canvasStyle}
      />
    </div>
  );
}
