/**
 * Voice-activity detection: decides when the caller starts and stops talking.
 *
 * Pure logic, no browser APIs, so it is testable with plain numbers. Feed it the
 * microphone's RMS level as it arrives and it reports the two moments that
 * matter for a hands-free call: speech started, and speech is over.
 *
 * It tracks the room's noise floor and sets its thresholds relative to it, so the
 * same code works in a quiet room and beside a fan. While the agent is speaking
 * it asks for louder, longer speech before reporting a start, so the agent's own
 * voice leaking back into the mic does not read as the caller cutting in.
 */

export interface VadOptions {
  /** Analysis window. Raw audio blocks are ~3ms, far too jittery to judge on. */
  frameMs: number;
  /** Speech this long, unbroken, counts as a start. */
  minSpeechMs: number;
  /** Same, while the agent is talking (a barge-in). */
  bargeInMinSpeechMs: number;
  /** Quiet this long after speech counts as the end of the turn. */
  endSilenceMs: number;
  /** A turn is cut here even if the caller never pauses. */
  maxUtteranceMs: number;
  /** Speech thresholds are this multiple of the noise floor... */
  onRatio: number;
  offRatio: number;
  /** ...but never below these absolute levels. */
  minOn: number;
  minOff: number;
  /** Threshold multiplier while the agent is speaking. */
  bargeInBoost: number;
}

export const DEFAULT_VAD: VadOptions = {
  frameMs: 20,
  minSpeechMs: 160,
  bargeInMinSpeechMs: 360,
  endSilenceMs: 850,
  maxUtteranceMs: 25_000,
  onRatio: 3,
  offRatio: 1.8,
  minOn: 0.02,
  minOff: 0.012,
  bargeInBoost: 1.8,
};

export type VadEvent =
  | { type: "start"; bargeIn: boolean }
  | {
      type: "end";
      /** Time above the quiet threshold: how much of the turn was actual speech. */
      voicedMs: number;
      /** The silence at the tail, which the caller can trim. */
      trailingSilenceMs: number;
    };

export class Vad {
  private readonly options: VadOptions;
  private speaking = false;
  private noiseFloor = 0.006;
  private calibratedMs = 0;
  private speechRunMs = 0;
  private silenceMs = 0;
  private utteranceMs = 0;
  private voicedMs = 0;
  private sumSquares = 0;
  private windowMs = 0;

  constructor(options: Partial<VadOptions> = {}) {
    this.options = { ...DEFAULT_VAD, ...options };
  }

  get isSpeaking(): boolean {
    return this.speaking;
  }

  /** Start over, e.g. after the caller was muted or a turn was handed off. */
  reset(): void {
    this.speaking = false;
    this.speechRunMs = 0;
    this.silenceMs = 0;
    this.utteranceMs = 0;
    this.voicedMs = 0;
    this.sumSquares = 0;
    this.windowMs = 0;
  }

  /**
   * Feed one audio block: its RMS level and how long it lasted, in seconds.
   * Returns whatever the block completed (usually nothing).
   */
  push(rms: number, seconds: number, agentSpeaking = false): VadEvent[] {
    this.sumSquares += rms * rms * seconds;
    this.windowMs += seconds * 1000;

    const events: VadEvent[] = [];
    while (this.windowMs >= this.options.frameMs) {
      const frameRms = Math.sqrt(this.sumSquares / (this.windowMs / 1000));
      const consumed = this.options.frameMs;
      this.sumSquares = 0;
      this.windowMs -= consumed;
      const event = this.step(frameRms, consumed, agentSpeaking);
      if (event) events.push(event);
    }
    return events;
  }

  private step(rms: number, ms: number, agentSpeaking: boolean): VadEvent | null {
    const o = this.options;
    const boost = agentSpeaking ? o.bargeInBoost : 1;
    const on = Math.max(o.minOn, this.noiseFloor * o.onRatio) * boost;
    const off = Math.max(o.minOff, this.noiseFloor * o.offRatio);

    if (!this.speaking) {
      // Learn the room: fast for the first half second, slowly afterwards, and
      // only from frames that are clearly not speech. Never while the agent is
      // talking: its own voice leaking back would be learned as "room noise" and
      // raise the bar until a real interruption could not clear it.
      if (rms < on && !agentSpeaking) {
        const rate = this.calibratedMs < 500 ? 0.2 : 0.02;
        this.noiseFloor += (rms - this.noiseFloor) * rate;
        this.calibratedMs += ms;
      }

      this.speechRunMs = rms > on ? this.speechRunMs + ms : 0;
      const needed = agentSpeaking ? o.bargeInMinSpeechMs : o.minSpeechMs;
      if (this.speechRunMs >= needed) {
        this.speaking = true;
        this.utteranceMs = this.speechRunMs;
        this.voicedMs = this.speechRunMs;
        this.silenceMs = 0;
        return { type: "start", bargeIn: agentSpeaking };
      }
      return null;
    }

    this.utteranceMs += ms;
    if (rms > off) {
      this.voicedMs += ms;
      this.silenceMs = 0;
    } else {
      this.silenceMs += ms;
    }

    const ended = this.silenceMs >= o.endSilenceMs || this.utteranceMs >= o.maxUtteranceMs;
    if (!ended) return null;

    const event: VadEvent = {
      type: "end",
      voicedMs: this.voicedMs,
      trailingSilenceMs: this.silenceMs,
    };
    this.reset();
    return event;
  }
}
