"use client";

import { usePathname } from "next/navigation";
import * as React from "react";

import { FLAKE_HEIGHT_RATIO, FLAKE_PATHS, LIGHT_BUCKETS, SPRITE_PAD, spriteFor } from "./flake-sprites";

const WIDTHS = [46, 58, 70, 82, 96, 54, 76, 64] as const;
const ASPECTS = [1, 0.85, 1.12, 0.95, 1.2, 0.9] as const;

/** The hero holds one flake per this many px². Below it the page carries the
 *  same flakes at DENSITY of that concentration, so the surface reads as one
 *  continuous field rather than a busy hero over a bare page. */
const HERO_PX_PER_FLAKE = 4400;
const DENSITY = 0.7;

/** Vertical overscan, so a flake wraps around off-screen instead of popping. */
const OVERSCAN = 130;

/** Low-discrepancy sequence in (0, 1) — an even scatter with no bare patches
 *  and no random layout to disagree between renders. */
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

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

/**
 * A fixed, window-sized canvas of drifting silk flakes behind the whole page:
 * the hero's field carried down the scroll, drawn from the same sprites.
 *
 * Each flake has its own depth, so scrolling moves it at a fraction of the
 * page's speed (parallax) and it wraps round vertically rather than ending.
 * It is one canvas layer that never scrolls with the page, so its cost stays
 * flat however long the page is and however many flakes it holds — unlike a
 * DOM element per flake, which the compositor has to sort on every scroll
 * frame. On the home page it fades in as the hero (which has its own, denser
 * field) fades out.
 */
export function PageFlakes() {
  const pathname = usePathname();
  const isHome = pathname === "/";
  const canvasRef = React.useRef<HTMLCanvasElement | null>(null);
  const [ready, setReady] = React.useState(false);

  // Mounted after first paint: it is decoration, never on the critical path.
  React.useEffect(() => {
    const idle = window.requestIdleCallback ?? ((cb: () => void) => window.setTimeout(cb, 300));
    const handle = idle(() => setReady(true));
    return () => {
      if (window.cancelIdleCallback) window.cancelIdleCallback(handle);
      else window.clearTimeout(handle);
    };
  }, []);

  React.useEffect(() => {
    const canvas = canvasRef.current;
    if (!ready || !canvas) return;
    const g = canvas.getContext("2d");
    if (!g) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let vw = 0;
    let vh = 0;
    let ratio = 1;
    let count = 0;

    type Flake = {
      hx: number;
      hy: number;
      depth: number;
      phase: number;
      speed: number;
      amp: number;
      w: number;
      h: number;
      scale: number;
      tilt: number;
      sprite: HTMLCanvasElement;
    };
    let flakes: Flake[] = [];

    function build() {
      vw = window.innerWidth;
      vh = window.innerHeight;
      ratio = Math.min(window.devicePixelRatio || 1, 1.5);
      canvas!.width = Math.round(vw * ratio);
      canvas!.height = Math.round(vh * ratio);

      const density = DENSITY;
      // Phones hold fewer: the same look, but a smaller budget per frame.
      const cap = vw < 640 ? 70 : 240;
      count = clamp(Math.round((vw * vh) / (HERO_PX_PER_FLAKE / density)), 20, cap);

      flakes = Array.from({ length: count }, (_, i) => {
        const w = WIDTHS[Math.floor(radical(i + 1, 7) * WIDTHS.length)]!;
        const aspect = ASPECTS[Math.floor(radical(i + 1, 11) * ASPECTS.length)]!;
        const variant = Math.floor(radical(i + 1, 5) * FLAKE_PATHS.length);
        const bucket = Math.floor(radical(i + 1, 19) * LIGHT_BUCKETS);
        return {
          hx: radical(i + 1, 3),
          hy: radical(i + 1, 2),
          depth: 0.18 + radical(i + 1, 13) * 0.62,
          phase: radical(i + 1, 23) * Math.PI * 2,
          speed: 0.18 + radical(i + 1, 29) * 0.3,
          amp: 5 + radical(i + 1, 31) * 11,
          w,
          h: w * FLAKE_HEIGHT_RATIO * aspect,
          scale: 0.85 + radical(i + 1, 17) * 0.3,
          tilt: (radical(i + 1, 37) - 0.5) * Math.PI,
          sprite: spriteFor(variant, bucket),
        };
      });
    }

    let clock = 0;
    let lastPaint = 0;
    let lastScroll = -1;

    function draw() {
      const sy = window.scrollY;
      // On the home page the layer fades in as the hero (with its own denser
      // field) scrolls away; elsewhere it is a constant, quieter backdrop.
      const ramp = isHome ? clamp((sy - vh * 0.25) / (vh * 0.7), 0, 1) : 1;
      const level = ramp * (isHome ? 0.9 : 0.5);
      canvas!.style.opacity = String(level);
      if (level <= 0.01) {
        lastScroll = -1;
        return;
      }

      g!.setTransform(1, 0, 0, 1, 0, 0);
      g!.clearRect(0, 0, canvas!.width, canvas!.height);
      const span = vh + OVERSCAN * 2;
      const scroll = reduced ? 0 : sy;

      for (const f of flakes) {
        const raw = f.hy * span - scroll * f.depth;
        const y = (((raw % span) + span) % span) - OVERSCAN;
        const t = clock * f.speed + f.phase;
        const x = f.hx * vw + Math.sin(t) * f.amp;
        const yy = y + Math.cos(t * 0.8) * f.amp * 0.6;
        const rot = f.tilt + Math.sin(t * 0.6) * 0.5;
        const squashX = Math.cos(Math.sin(t * 0.7) * 1.05);
        const squashY = Math.cos(Math.sin(t * 0.5) * 0.55);
        const sx = f.scale * squashX * ratio;
        const sc = f.scale * squashY * ratio;
        const cos = Math.cos(rot);
        const sin = Math.sin(rot);
        g!.globalAlpha = 0.55 + f.depth * 0.4;
        g!.setTransform(cos * sx, sin * sx, -sin * sc, cos * sc, x * ratio, yy * ratio);
        const dw = f.w * (1 + (2 * SPRITE_PAD) / 100);
        const dh = f.h * (1 + (2 * SPRITE_PAD) / 60);
        g!.drawImage(f.sprite, -dw / 2, -dh / 2, dw, dh);
      }
      lastScroll = sy;
    }

    build();
    draw();

    let frame = 0;
    function tick(now: number) {
      frame = requestAnimationFrame(tick);
      if (document.hidden) return;
      // Drift is slow, so ~30fps is indistinguishable at rest; while the page
      // is actually scrolling, draw every frame so parallax stays smooth.
      const scrolling = window.scrollY !== lastScroll;
      if (!scrolling && now - lastPaint < 33) return;
      const dt = Math.min(0.05, (now - lastPaint) / 1000);
      lastPaint = now;
      if (!reduced) clock += dt;
      draw();
    }
    if (!reduced) frame = requestAnimationFrame(tick);
    else window.addEventListener("scroll", draw, { passive: true });

    let timer: ReturnType<typeof setTimeout> | undefined;
    function onResize() {
      clearTimeout(timer);
      timer = setTimeout(() => {
        build();
        draw();
      }, 150);
    }
    window.addEventListener("resize", onResize);

    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(timer);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", draw);
    };
  }, [ready, isHome]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      className="pointer-events-none fixed inset-0 z-0 size-full opacity-0"
    />
  );
}
