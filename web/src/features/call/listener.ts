/**
 * Hands-free listening: keeps the microphone open for the whole call and cuts it
 * into caller turns on its own, so nobody has to tap to talk or tap to send.
 *
 * Audio only leaves the browser once a turn is finished. A short rolling buffer
 * of recent audio is kept while idle and prepended when speech is detected, so
 * the first syllable is never clipped by the time the detector is sure it heard
 * someone speaking.
 */

import { encodeWav, openMic, toDisplayLevel, type MicSession, type Recording } from "./recorder";
import { Vad } from "./vad";

/** Audio kept from before speech was confirmed; must cover the barge-in delay. */
const PRE_ROLL_SECONDS = 0.8;
/** Silence left on the end of a turn: enough to sound natural, no more. */
const TRAILING_SILENCE_SECONDS = 0.25;
/** Below this much actual speech a "turn" is a cough, a click or a chair. */
const MIN_VOICED_SECONDS = 0.3;

export interface ListenerEvents {
  /** ~every few ms while open, 0-1, for the waveform. */
  onLevel?: (level: number) => void;
  /** The caller began speaking. `bargeIn` is true if the agent was talking. */
  onSpeechStart?: (info: { bargeIn: boolean }) => void;
  /** A finished caller turn, encoded and ready to send. */
  onUtterance: (recording: Recording) => void;
}

export class HandsFreeListener {
  private session: MicSession | null = null;
  private readonly vad = new Vad();
  private preRoll: Float32Array[] = [];
  private preRollSamples = 0;
  private turn: Float32Array[] | null = null;
  private agentSpeaking = false;
  private paused = false;

  constructor(private readonly events: ListenerEvents) {}

  get isOpen(): boolean {
    return this.session !== null;
  }

  async start(): Promise<void> {
    this.session = await openMic((frame, rms) => this.onFrame(frame, rms));
  }

  /** True while the agent's voice is playing: raises the bar for a barge-in. */
  setAgentSpeaking(speaking: boolean): void {
    this.agentSpeaking = speaking;
  }

  /** Ignore the microphone (muted, or a turn is already being processed). */
  setPaused(paused: boolean): void {
    if (paused === this.paused) return;
    this.paused = paused;
    if (paused) this.discard();
  }

  async stop(): Promise<void> {
    await this.session?.close();
    this.session = null;
    this.discard();
    this.events.onLevel?.(0);
  }

  private discard(): void {
    this.vad.reset();
    this.turn = null;
    this.preRoll = [];
    this.preRollSamples = 0;
  }

  private onFrame(frame: Float32Array, rms: number): void {
    const sampleRate = this.session?.sampleRate;
    if (!sampleRate) return;
    this.events.onLevel?.(this.paused ? 0 : toDisplayLevel(rms));
    if (this.paused) return;

    if (this.turn) {
      this.turn.push(frame);
    } else {
      this.preRoll.push(frame);
      this.preRollSamples += frame.length;
      const cap = PRE_ROLL_SECONDS * sampleRate;
      while (this.preRollSamples - (this.preRoll[0]?.length ?? 0) >= cap) {
        this.preRollSamples -= this.preRoll.shift()?.length ?? 0;
      }
    }

    for (const event of this.vad.push(rms, frame.length / sampleRate, this.agentSpeaking)) {
      if (event.type === "start") {
        this.turn = [...this.preRoll];
        this.preRoll = [];
        this.preRollSamples = 0;
        this.events.onSpeechStart?.({ bargeIn: event.bargeIn });
      } else {
        this.finishTurn(event.voicedMs / 1000, event.trailingSilenceMs / 1000, sampleRate);
      }
    }
  }

  private finishTurn(voicedSeconds: number, trailingSeconds: number, sampleRate: number): void {
    const chunks = this.turn;
    this.turn = null;
    if (!chunks || voicedSeconds < MIN_VOICED_SECONDS) return;

    // Drop the silence the detector waited through to be sure the caller was done.
    let excess = Math.max(0, trailingSeconds - TRAILING_SILENCE_SECONDS) * sampleRate;
    while (excess > 0 && chunks.length > 0) {
      const last = chunks[chunks.length - 1];
      if (!last || last.length > excess) break;
      excess -= last.length;
      chunks.pop();
    }

    const samples = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    this.events.onUtterance({
      blob: encodeWav(chunks, sampleRate),
      durationSeconds: samples / sampleRate,
    });
  }
}
