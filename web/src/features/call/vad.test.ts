import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { Vad, type VadEvent } from "./vad.ts";

const BLOCK = 128 / 48_000; // one audio block at 48kHz, in seconds

/** Feed a constant level for `ms`, collecting whatever events come out. */
function feed(vad: Vad, level: number, ms: number, agentSpeaking = false): VadEvent[] {
  const events: VadEvent[] = [];
  for (let t = 0; t < ms / 1000; t += BLOCK) {
    events.push(...vad.push(level, BLOCK, agentSpeaking));
  }
  return events;
}

const ROOM = 0.004;
const SPEECH = 0.08;

describe("Vad", () => {
  it("stays quiet in a quiet room", () => {
    const vad = new Vad();
    assert.deepEqual(feed(vad, ROOM, 5000), []);
  });

  it("reports a start, then an end after the pause", () => {
    const vad = new Vad();
    feed(vad, ROOM, 600);
    const speech = feed(vad, SPEECH, 1200);
    assert.deepEqual(speech, [{ type: "start", bargeIn: false }]);

    const after = feed(vad, ROOM, 1200);
    assert.equal(after.length, 1);
    const [end] = after;
    assert.equal(end?.type, "end");
    if (end?.type === "end") {
      assert.ok(end.voicedMs >= 1100, `voiced ${end.voicedMs}`);
      assert.ok(end.trailingSilenceMs >= 850);
    }
  });

  it("ignores a click that is too short to be speech", () => {
    const vad = new Vad();
    feed(vad, ROOM, 600);
    assert.deepEqual(feed(vad, SPEECH, 60), []);
    assert.deepEqual(feed(vad, ROOM, 2000), []);
  });

  it("does not end a turn on a short pause between words", () => {
    const vad = new Vad();
    feed(vad, ROOM, 600);
    feed(vad, SPEECH, 800);
    assert.deepEqual(feed(vad, ROOM, 400), []); // a breath
    assert.deepEqual(feed(vad, SPEECH, 800), []); // still the same turn
    assert.equal(feed(vad, ROOM, 1200).length, 1);
  });

  it("scales its thresholds to a noisy room", () => {
    const vad = new Vad();
    const noisy = 0.02; // a fan: above the quiet-room minimum
    assert.deepEqual(feed(vad, noisy, 3000), []);
    assert.deepEqual(feed(vad, 0.15, 800), [{ type: "start", bargeIn: false }]);
  });

  it("asks for more before reporting the caller cutting across the agent", () => {
    const vad = new Vad();
    feed(vad, ROOM, 600);
    // Faint leakage of the agent's own voice: speech-like, but not loud enough.
    assert.deepEqual(feed(vad, 0.03, 2000, true), []);
    // A real interruption is loud and lasts.
    assert.deepEqual(feed(vad, 0.12, 700, true), [{ type: "start", bargeIn: true }]);
  });

  it("cuts a turn that never pauses", () => {
    const vad = new Vad({ maxUtteranceMs: 3000 });
    feed(vad, ROOM, 600);
    const events = feed(vad, SPEECH, 4000);
    assert.equal(events.filter((e) => e.type === "end").length, 1);
  });

  it("can be reset mid-turn", () => {
    const vad = new Vad();
    feed(vad, ROOM, 600);
    feed(vad, SPEECH, 600);
    assert.equal(vad.isSpeaking, true);
    vad.reset();
    assert.equal(vad.isSpeaking, false);
    assert.deepEqual(feed(vad, ROOM, 2000), []);
  });
});
