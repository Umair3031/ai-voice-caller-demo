/**
 * The silk-chip sprites both flake layers stamp: the hero's orbit around the
 * mic and the page-wide drift behind everything else. One drawing, so the
 * two layers are the same flakes and read as one continuous surface.
 */

/** Flake artboard, drawn 100 x 60. Classic-chip outlines in three degrees of
 *  ruffle so the set isn't one repeated silhouette: two smooth, four
 *  gently wavy, two heavily ruffled. */
export const FLAKE_HEIGHT_RATIO = 0.6;
export const FLAKE_PATHS = [
  // smooth
  "M6,26 C4,10 22,2 42,4 C62,6 80,14 92,28 C98,36 92,48 76,51 C56,55 30,52 14,44 C8,40 7,33 6,26 Z",
  "M6,24 C4,10 20,1 36,4 C52,7 62,20 78,33 C88,40 98,42 96,48 C94,53 82,51 70,47 C52,42 36,44 22,40 C10,36 7,31 6,24 Z",
  // gently wavy
  "M5,26 C3,11 19,2 35,5 C51,8 64,17 80,27 C91,33 99,34 97,40 C95,46 83,46 70,44 C50,41 34,46 20,42 C9,38 6,33 5,26 Z",
  "M6,26 C5,12 20,3 36,5 C46,1 56,8 64,12 C70,10 78,16 84,24 C90,28 98,32 96,40 C93,46 86,44 80,48 C72,52 62,47 54,49 C44,53 34,46 26,44 C14,42 7,34 6,26 Z",
  "M5,27 C4,14 16,5 30,6 C38,2 48,7 58,10 C66,8 76,14 82,22 C88,26 96,30 97,38 C97,45 88,48 78,49 C70,53 62,49 52,48 C42,52 32,47 24,45 C12,42 6,34 5,27 Z",
  "M7,22 C6,9 22,1 38,5 C54,9 60,24 74,36 C84,44 97,44 95,50 C92,55 78,53 66,49 C48,43 34,45 20,39 C10,34 8,29 7,22 Z",
  // heavily ruffled
  "M5,28 C3,20 8,12 16,9 C20,4 28,5 32,2 C40,0 46,6 54,4 C62,2 66,9 74,12 C80,10 88,16 90,24 C97,28 98,36 92,42 C93,48 84,54 76,52 C70,57 60,55 54,52 C46,56 36,52 30,50 C22,53 14,46 12,42 C6,40 4,34 5,28 Z",
  "M4,30 C2,22 10,14 20,12 C24,6 34,4 40,7 C48,2 58,6 64,10 C72,8 84,14 90,22 C98,28 98,38 90,44 C86,50 76,50 70,48 C62,54 50,52 44,48 C36,52 24,50 20,44 C10,44 5,38 4,30 Z",
] as const;

/** How many directions the lit-side fade comes in (45 degrees apart). */
export const LIGHT_BUCKETS = 8;

export function bucketOf(lightAngle: number): number {
  const normalized = ((lightAngle % 360) + 360) % 360;
  return Math.round(normalized / (360 / LIGHT_BUCKETS)) % LIGHT_BUCKETS;
}

/** Sprite resolution (px per artboard unit) and the transparent border that
 *  holds the soft white glow around each chip. */
const SPRITE_K = 2;
export const SPRITE_PAD = 14;
const GLOW_BLUR = 7;

const spriteCache = new Map<number, HTMLCanvasElement>();

function makeCanvas(): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = (100 + 2 * SPRITE_PAD) * SPRITE_K;
  canvas.height = (60 + 2 * SPRITE_PAD) * SPRITE_K;
  return canvas;
}

/** Fill `path` with a gradient laid over the 100 x 60 artboard, elliptical
 *  like an objectBoundingBox radial gradient (clipped, since a canvas gradient
 *  lives in the transform current at fill time). */
function fillRadial(
  g: CanvasRenderingContext2D,
  path: Path2D,
  stops: ReadonlyArray<readonly [number, string]>,
) {
  g.save();
  g.clip(path);
  g.translate(50, 30);
  g.scale(1, 0.6);
  const gradient = g.createRadialGradient(0, 0, 0, 0, 0, 62);
  for (const [offset, color] of stops) gradient.addColorStop(offset, color);
  g.fillStyle = gradient;
  g.fillRect(-100, -100, 300, 300);
  g.restore();
}

/**
 * One pre-rendered silk chip: cream-white body easing to lake-teal, a faint
 * tint, a dot texture that fades toward the side facing the mic's light, a
 * fold of shading, a highlight, and a soft white glow. Drawn once per
 * (outline, light direction) and then stamped hundreds of times per frame —
 * the same look the old per-flake SVG had, without hundreds of DOM layers for
 * the compositor to sort on every scroll frame.
 */
export function spriteFor(variant: number, bucket: number): HTMLCanvasElement {
  const key = variant * LIGHT_BUCKETS + bucket;
  const cached = spriteCache.get(key);
  if (cached) return cached;

  const path = new Path2D(FLAKE_PATHS[variant]!);
  const body = makeCanvas();
  const b = body.getContext("2d")!;
  b.scale(SPRITE_K, SPRITE_K);
  b.translate(SPRITE_PAD, SPRITE_PAD);

  fillRadial(b, path, [
    [0, "#ffffff"],
    [0.5, "#fdfbf4"],
    [0.82, "#d9f0ee"],
    [1, "#6fc2c9"],
  ]);
  b.lineJoin = "round";
  b.lineWidth = 1.8;
  b.strokeStyle = "#ffffff";
  b.stroke(path);
  fillRadial(b, path, [
    [0.55, "rgba(111,194,201,0)"],
    [1, "rgba(111,194,201,0.28)"],
  ]);

  // Dot texture, faded toward the far side by a gradient mask.
  const dots = makeCanvas();
  const d = dots.getContext("2d")!;
  d.scale(SPRITE_K, SPRITE_K);
  d.translate(SPRITE_PAD, SPRITE_PAD);
  d.save();
  d.clip(path);
  d.fillStyle = "rgba(63,168,166,0.6)";
  for (let y = 3; y < 60; y += 6) {
    for (let x = 3; x < 100; x += 6) {
      d.beginPath();
      d.arc(x, y, 1.15, 0, Math.PI * 2);
      d.fill();
    }
  }
  d.restore();
  const rad = (bucket * (360 / LIGHT_BUCKETS) * Math.PI) / 180;
  const dx = Math.cos(rad) * 0.5;
  const dy = Math.sin(rad) * 0.5;
  const fade = d.createLinearGradient(
    100 * (0.5 - dx),
    60 * (0.5 - dy),
    100 * (0.5 + dx),
    60 * (0.5 + dy),
  );
  fade.addColorStop(0, "rgba(0,0,0,0.15)");
  fade.addColorStop(0.55, "rgba(0,0,0,0.5)");
  fade.addColorStop(1, "rgba(0,0,0,1)");
  d.globalCompositeOperation = "destination-in";
  d.fillStyle = fade;
  d.fillRect(-SPRITE_PAD, -SPRITE_PAD, 100 + 2 * SPRITE_PAD, 60 + 2 * SPRITE_PAD);
  b.save();
  b.setTransform(1, 0, 0, 1, 0, 0);
  b.drawImage(dots, 0, 0);
  b.restore();

  const fold = b.createLinearGradient(10, 0, 90, 60);
  fold.addColorStop(0.15, "rgba(255,255,255,0)");
  fold.addColorStop(0.4, "rgba(255,255,255,0.55)");
  fold.addColorStop(0.68, "rgba(87,184,182,0.18)");
  fold.addColorStop(0.9, "rgba(87,184,182,0)");
  b.fillStyle = fold;
  b.fill(path);

  b.beginPath();
  b.moveTo(12, 14);
  b.bezierCurveTo(22, 6, 38, 6, 50, 12);
  b.strokeStyle = "rgba(255,255,255,0.95)";
  b.lineWidth = 2.2;
  b.lineCap = "round";
  b.stroke();

  const sprite = makeCanvas();
  const g = sprite.getContext("2d")!;
  g.shadowColor = "rgba(255,255,255,1)";
  g.shadowBlur = GLOW_BLUR * SPRITE_K;
  g.drawImage(body, 0, 0);
  spriteCache.set(key, sprite);
  return sprite;
}

