/**
 * PCM capture worklet.
 *
 * Runs on the audio rendering thread and forwards raw mono Float32 frames to
 * the main thread, plus an RMS level per block so the waveform can show the
 * caller's actual voice rather than a decorative animation.
 *
 * This replaces `ScriptProcessorNode`, which is deprecated and runs its
 * callback on the main thread — so a busy UI could drop audio frames mid-turn.
 */
class PcmRecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._running = true;
    this.port.onmessage = (event) => {
      if (event.data === "stop") this._running = false;
    };
  }

  process(inputs) {
    if (!this._running) return false;

    const channels = inputs[0];
    if (!channels || channels.length === 0) return true;

    const samples = channels[0];
    if (!samples || samples.length === 0) return true;

    // Copy: the input buffer is reused by the audio thread on the next block.
    const frame = new Float32Array(samples);

    let sumOfSquares = 0;
    for (let i = 0; i < frame.length; i += 1) sumOfSquares += frame[i] * frame[i];
    const rms = Math.sqrt(sumOfSquares / frame.length);

    // Transfer the buffer rather than structured-cloning it: capture runs at
    // ~375 blocks/second and copying each one would be pure waste.
    this.port.postMessage({ frame, rms }, [frame.buffer]);
    return true;
  }
}

registerProcessor("pcm-recorder", PcmRecorderProcessor);
