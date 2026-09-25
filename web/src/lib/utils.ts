import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge class names with Tailwind conflict resolution (later wins). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** `1284.3` -> `"1.28 s"`, `412` -> `"412 ms"`. Keeps latency readable at both scales. */
export function formatDuration(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  return `${Math.round(ms)} ms`;
}

/** Seconds -> `mm:ss`, for the call timer. */
export function formatClock(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export function formatPercent(ratio: number): string {
  return `${Math.round(ratio * 100)}%`;
}

export function formatCurrency(amount: number, currency: string): string {
  try {
    // Only a Nigerian locale gives the naira its symbol; en-US prints the bare
    // code with the sign in front of it ("-NGN 4,500.00").
    return new Intl.NumberFormat(currency === "NGN" ? "en-NG" : "en-US", {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    // Intl throws on an unrecognized currency code; the raw figure still reads.
    return `${amount.toFixed(2)} ${currency}`;
  }
}

/** Decodes the engine's base64 reply audio into a playable object URL. */
export function base64ToBlobUrl(base64: string, mediaType = "audio/wav"): string {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return URL.createObjectURL(new Blob([bytes], { type: mediaType }));
}
