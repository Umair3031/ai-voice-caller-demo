/**
 * Microphone capture and WAV encoding.
 *
 * Captures mono PCM through an AudioWorklet, reports a live level for the
 * waveform, and encodes a 16kHz 16-bit WAV — the container the engine's STT
 * accepts, produced with no external dependency.
 */

const WORKLET_URL = "/worklets/pcm-recorder.js";
const WORKLET_NAME = "pcm-recorder";

/** Below this, a "recording" is a mis-tap rather than an utterance. */
export const MIN_TURN_SECONDS = 0.35;

export class MicrophonePermissionError extends Error {
  constructor() {
    super("Microphone access was not granted.");
    this.name = "MicrophonePermissionError";
  }
}

export class MicrophoneUnsupportedError extends Error {
  constructor(detail: string) {
    super(detail);
    this.name = "MicrophoneUnsupportedError";
  }
}

export interface Recording {
  blob: Blob;
  durationSeconds: number;
}

/** Speech recognition works on 16kHz mono. Sending the browser's native 44.1 or
 *  48kHz gains it nothing and triples the upload. */
export const SPEECH_SAMPLE_RATE = 16_000;

/** One block of microphone audio and its RMS level. */
export type FrameHandler = (frame: Float32Array, rms: number) => void;

export interface MicSession {
  sampleRate: number;
  /** Stops capture and releases the microphone. Safe to call twice. */
  close(): Promise<void>;
}

/**
 * Opens the microphone and streams mono PCM blocks to `onFrame` until closed.
 *
 * Shared by tap-to-talk (`Recorder`) and hands-free (`HandsFreeListener`), so both
 * get the same permission handling, Safari resume, and guaranteed release of the
 * device on every exit path.
 */
export async function openMic(onFrame: FrameHandler): Promise<MicSession> {
  if (typeof window === "undefined" || !navigator.mediaDevices?.getUserMedia) {
    throw new MicrophoneUnsupportedError(
      "This browser cannot access a microphone. Note that it requires a secure (https) origin.",
    );
  }

  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch {
    throw new MicrophonePermissionError();
  }
  const releaseStream = () => stream.getTracks().forEach((track) => track.stop());

  const AudioContextCtor =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextCtor) {
    releaseStream();
    throw new MicrophoneUnsupportedError("This browser has no Web Audio support.");
  }

  const context = new AudioContextCtor();
  // Safari starts contexts suspended until a user gesture resumes them.
  if (context.state === "suspended") await context.resume();

  try {
    await context.audioWorklet.addModule(WORKLET_URL);
  } catch (error) {
    releaseStream();
    await context.close().catch(() => undefined);
    throw new MicrophoneUnsupportedError(
      `Could not load the audio recorder (${error instanceof Error ? error.message : "unknown"}).`,
    );
  }

  const source = context.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(context, WORKLET_NAME, {
    numberOfInputs: 1,
    numberOfOutputs: 0,
    channelCount: 1,
  });
  node.port.onmessage = (event: MessageEvent<{ frame: Float32Array; rms: number }>) => {
    onFrame(event.data.frame, event.data.rms);
  };
  // The worklet declares zero outputs, so it needs no connection to the
  // destination — and therefore cannot feed the mic back to the speakers.
  source.connect(node);

  let closed = false;
  return {
    sampleRate: context.sampleRate,
    async close() {
      if (closed) return;
      closed = true;
      node.port.postMessage("stop");
      node.port.onmessage = null;
      node.disconnect();
      source.disconnect();
      // Always release the mic: leaving it open keeps the browser's recording
      // indicator lit and holds the device against other apps.
      releaseStream();
      if (context.state !== "closed") await context.close().catch(() => undefined);
    },
  };
}

export interface RecorderOptions {
  /** Called ~every 2.7ms of audio with a 0-1 level, for the waveform. */
  onLevel?: (level: number) => void;
}

/** Perceptual, not linear: a square-root curve keeps normal speech visible
 *  instead of pinning the meter near zero. */
export function toDisplayLevel(rms: number): number {
  return Math.min(1, Math.sqrt(rms) * 2.2);
}

/** Tap-to-talk: records from `start()` until `stop()`. */
export class Recorder {
  private session: MicSession | null = null;
  private chunks: Float32Array[] = [];
  private sampleCount = 0;
  private readonly onLevel?: (level: number) => void;

  constructor(options: RecorderOptions = {}) {
    this.onLevel = options.onLevel;
  }

  async start(): Promise<void> {
    this.chunks = [];
    this.sampleCount = 0;
    this.session = await openMic((frame, rms) => {
      this.chunks.push(frame);
      this.sampleCount += frame.length;
      this.onLevel?.(toDisplayLevel(rms));
    });
  }

  async stop(): Promise<Recording> {
    const sampleRate = this.session?.sampleRate ?? 48_000;
    const durationSeconds = this.sampleCount / sampleRate;
    const blob = encodeWav(this.chunks, sampleRate);
    await this.teardown();
    return { blob, durationSeconds };
  }

  /** Abandon the take without encoding anything (e.g. the caller hung up). */
  async cancel(): Promise<void> {
    await this.teardown();
  }

  private async teardown(): Promise<void> {
    await this.session?.close();
    this.session = null;
    this.chunks = [];
    this.sampleCount = 0;
    this.onLevel?.(0);
  }
}

/**
 * Downsamples by averaging each output sample's span of input samples — a box
 * filter, which also low-passes so the higher frequencies do not fold back as
 * noise. A source already at or below the target rate is returned unchanged.
 */
export function resample(input: Float32Array, fromRate: number, toRate: number): Float32Array {
  if (fromRate <= toRate) return input;
  const ratio = fromRate / toRate;
  const length = Math.floor(input.length / ratio);
  const output = new Float32Array(length);
  for (let i = 0; i < length; i += 1) {
    const from = Math.floor(i * ratio);
    const to = Math.min(input.length, Math.max(from + 1, Math.floor((i + 1) * ratio)));
    let sum = 0;
    for (let j = from; j < to; j += 1) sum += input[j] ?? 0;
    output[i] = sum / (to - from);
  }
  return output;
}

/** Float32 chunks -> a 16-bit PCM mono WAV at speech-recognition quality. */
export function encodeWav(
  chunks: Float32Array[],
  sampleRate: number,
  targetRate: number = SPEECH_SAMPLE_RATE,
): Blob {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const joined = new Float32Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    joined.set(chunk, offset);
    offset += chunk.length;
  }
  const merged = resample(joined, sampleRate, targetRate);
  const outputRate = Math.min(sampleRate, targetRate);

  const buffer = new ArrayBuffer(44 + merged.length * 2);
  const view = new DataView(buffer);

  const writeString = (position: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(position + i, text.charCodeAt(i));
  };

  const dataBytes = merged.length * 2;
  writeString(0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true); // PCM header size
  view.setUint16(20, 1, true); // format: PCM
  view.setUint16(22, 1, true); // channels: mono
  view.setUint32(24, outputRate, true);
  view.setUint32(28, outputRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeString(36, "data");
  view.setUint32(40, dataBytes, true);

  let position = 44;
  for (let i = 0; i < merged.length; i += 1) {
    // Clamp before scaling: values outside [-1, 1] would wrap and click.
    const sample = Math.max(-1, Math.min(1, merged[i] ?? 0));
    view.setInt16(position, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    position += 2;
  }

  return new Blob([view], { type: "audio/wav" });
}
