"use client";

import * as React from "react";

import { ApiError, endSession, openCall, runTurn } from "@/lib/api/client";
import type { CallerOut, PersonaOut, TurnResponse } from "@/lib/api/types";
import { base64ToBlobUrl } from "@/lib/utils";
import { HandsFreeListener } from "./listener";
import {
  MIN_TURN_SECONDS,
  MicrophonePermissionError,
  MicrophoneUnsupportedError,
  Recorder,
} from "./recorder";

export type CallPhase =
  | "idle"
  /** Dialling: the agent's greeting is on its way. */
  | "connecting"
  /** Hands-free, mic open, waiting for the caller to speak. */
  | "listening"
  /** The caller is speaking (hands-free) or holding the mic (tap-to-talk). */
  | "recording"
  | "sending"
  | "speaking"
  /** Tap-to-talk, waiting for a tap. */
  | "ready"
  | "error";

export type CallMode = "handsfree" | "manual";

export interface TranscriptEntry {
  id: string;
  role: "user" | "agent" | "system";
  text: string;
  /** Present on agent turns that came back as text because synthesis failed. */
  degraded?: boolean;
}

interface CallState {
  phase: CallPhase;
  mode: CallMode;
  muted: boolean;
  persona: PersonaOut | null;
  /** Who the demo assigned as the caller; null for a pre-sales prospect. */
  caller: CallerOut | null;
  sessionId: string | null;
  entries: TranscriptEntry[];
  error: string | null;
  elapsedSeconds: number;
  lastTurn: TurnResponse | null;
}

type Action =
  | { type: "start"; persona: PersonaOut }
  | { type: "opened"; turn: TurnResponse; caller: CallerOut | null }
  | { type: "end" }
  | { type: "phase"; phase: CallPhase }
  | { type: "mode"; mode: CallMode; phase: CallPhase }
  | { type: "muted"; muted: boolean }
  | { type: "append"; entry: Omit<TranscriptEntry, "id"> }
  | { type: "turn"; turn: TurnResponse }
  | { type: "error"; message: string; phase: CallPhase }
  | { type: "clearError" }
  | { type: "tick" };

const INITIAL: CallState = {
  phase: "idle",
  mode: "handsfree",
  muted: false,
  persona: null,
  caller: null,
  sessionId: null,
  entries: [],
  error: null,
  elapsedSeconds: 0,
  lastTurn: null,
};

let entrySequence = 0;
function nextEntryId(): string {
  entrySequence += 1;
  return `entry-${entrySequence}`;
}

function reducer(state: CallState, action: Action): CallState {
  switch (action.type) {
    case "start":
      return { ...INITIAL, phase: "connecting", persona: action.persona };
    case "opened":
      return {
        ...state,
        sessionId: action.turn.session_id,
        caller: action.caller,
        lastTurn: action.turn,
        entries: [
          ...state.entries,
          {
            id: nextEntryId(),
            role: "agent",
            text: action.turn.response_text,
            degraded: action.turn.outcome === "degraded_text_only",
          },
        ],
      };
    case "end":
      return INITIAL;
    case "phase":
      return { ...state, phase: action.phase };
    case "mode":
      return { ...state, mode: action.mode, phase: action.phase, muted: false };
    case "muted":
      return { ...state, muted: action.muted };
    case "append":
      return { ...state, entries: [...state.entries, { id: nextEntryId(), ...action.entry }] };
    case "turn": {
      const additions: TranscriptEntry[] = [];
      if (action.turn.transcript) {
        additions.push({ id: nextEntryId(), role: "user", text: action.turn.transcript });
      }
      if (action.turn.response_text) {
        additions.push({
          id: nextEntryId(),
          role: "agent",
          text: action.turn.response_text,
          degraded: action.turn.outcome === "degraded_text_only",
        });
      }
      return {
        ...state,
        sessionId: action.turn.session_id,
        entries: [...state.entries, ...additions],
        lastTurn: action.turn,
        error: null,
      };
    }
    case "error":
      return { ...state, phase: action.phase, error: action.message };
    case "clearError":
      return { ...state, error: null };
    case "tick":
      return { ...state, elapsedSeconds: state.elapsedSeconds + 1 };
    default:
      return state;
  }
}

function describeError(error: unknown): string {
  if (error instanceof MicrophonePermissionError) {
    return "Microphone access was blocked, so I switched to tap-to-talk. Allow the mic in your browser to go hands-free.";
  }
  if (error instanceof MicrophoneUnsupportedError) return error.message;
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong on that turn.";
}

/** A tiny silent clip. Playing it inside the click that starts the call unlocks
 *  audio for the rest of the call — Safari refuses to autoplay the greeting
 *  otherwise, since it arrives after a network round trip. */
function silentWavUrl(): string {
  const view = new DataView(new ArrayBuffer(46));
  const text = (at: number, value: string) =>
    [...value].forEach((ch, i) => view.setUint8(at + i, ch.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 38, true);
  text(8, "WAVEfmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 22_050, true);
  view.setUint32(28, 44_100, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, 2, true);
  return URL.createObjectURL(new Blob([view], { type: "audio/wav" }));
}

/**
 * Drives one call: the agent's opening, hands-free turn-taking (or tap-to-talk),
 * reply playback, and teardown.
 *
 * Hands-free keeps the mic open and lets `HandsFreeListener` decide when the
 * caller has finished a turn. While the agent is speaking the listener is still
 * live, so the caller can cut in: the reply stops and their words become the next
 * turn. Anything said while a reply is still being fetched is queued rather than
 * lost.
 *
 * All transient handles (recorder, listener, audio element, object URLs, timers)
 * live in refs and are released on every exit path, so ending a call never
 * leaves the microphone open or an object URL leaked.
 */
export function useCall() {
  const [state, dispatch] = React.useReducer(reducer, INITIAL);
  const [level, setLevel] = React.useState(0);

  const recorderRef = React.useRef<Recorder | null>(null);
  const listenerRef = React.useRef<HandsFreeListener | null>(null);
  const audioRef = React.useRef<HTMLAudioElement | null>(null);
  const audioUrlRef = React.useRef<string | null>(null);
  /** Turns the caller produced while a reply was still being fetched. */
  const queueRef = React.useRef<{ audio?: Blob; text?: string }[]>([]);
  /** True while the caller is typing: the mic is ignored so keystrokes are not
   *  heard as speech and sent as spurious turns. */
  const typingRef = React.useRef(false);
  const busyRef = React.useRef(false);
  /** Bumped on every start and end, so a slow response from a call that has
   *  already been hung up cannot resurrect it. */
  const callTokenRef = React.useRef(0);

  // Read through refs inside callbacks so the handlers stay stable.
  const stateRef = React.useRef(state);
  stateRef.current = state;

  const isActive = state.phase !== "idle";

  React.useEffect(() => {
    if (!isActive) return;
    const timer = window.setInterval(() => dispatch({ type: "tick" }), 1000);
    return () => window.clearInterval(timer);
  }, [isActive]);

  const idlePhase = React.useCallback(
    (): CallPhase => (stateRef.current.mode === "handsfree" ? "listening" : "ready"),
    [],
  );

  const stopPlayback = React.useCallback(() => {
    listenerRef.current?.setAgentSpeaking(false);
    if (audioRef.current) {
      audioRef.current.onended = null;
      audioRef.current.pause();
      audioRef.current.removeAttribute("src");
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }
  }, []);

  const playReply = React.useCallback(
    async (turn: TurnResponse) => {
      if (!turn.audio_base64) {
        dispatch({ type: "phase", phase: idlePhase() });
        return;
      }
      stopPlayback();

      const url = base64ToBlobUrl(turn.audio_base64, turn.audio_media_type ?? "audio/wav");
      audioUrlRef.current = url;

      const audio = audioRef.current ?? new Audio();
      audioRef.current = audio;
      audio.src = url;
      audio.onended = () => {
        listenerRef.current?.setAgentSpeaking(false);
        dispatch({ type: "phase", phase: idlePhase() });
      };

      dispatch({ type: "phase", phase: "speaking" });
      listenerRef.current?.setAgentSpeaking(true);
      try {
        await audio.play();
      } catch {
        // Autoplay can be blocked; the reply is already on screen as text, so
        // the turn is not lost.
        listenerRef.current?.setAgentSpeaking(false);
        dispatch({ type: "phase", phase: idlePhase() });
      }
    },
    [idlePhase, stopPlayback],
  );

  const submitTurn = React.useCallback(
    async (payload: { audio?: Blob; text?: string }) => {
      const { persona, caller } = stateRef.current;
      if (!persona) return;
      const token = callTokenRef.current;

      busyRef.current = true;
      dispatch({ type: "phase", phase: "sending" });
      try {
        const turn = await runTurn({
          phoneNumber: caller?.phone_number,
          persona: persona.id,
          sessionId: stateRef.current.sessionId,
          ...payload,
        });
        if (token !== callTokenRef.current) return;
        dispatch({ type: "turn", turn });

        if (turn.outcome === "degraded_text_only") {
          dispatch({
            type: "append",
            entry: {
              role: "system",
              text: turn.degraded_reason ?? "Voice was unavailable this turn.",
            },
          });
        }

        // The caller kept talking while this reply was being made. It is already
        // out of date, so skip speaking it and answer what they said since.
        const next = queueRef.current.shift();
        if (next) {
          busyRef.current = false;
          await submitTurn(next);
          return;
        }
        busyRef.current = false;
        await playReply(turn);
      } catch (error) {
        if (token !== callTokenRef.current) return;
        busyRef.current = false;
        queueRef.current = [];
        // Hands-free: silence or a cough is not an error worth showing. Just keep
        // listening.
        if (error instanceof ApiError && error.code === "no_speech_detected") {
          dispatch({ type: "phase", phase: idlePhase() });
          return;
        }
        dispatch({ type: "error", message: describeError(error), phase: idlePhase() });
      }
    },
    [idlePhase, playReply],
  );

  const beginListening = React.useCallback(async () => {
    const listener = new HandsFreeListener({
      onLevel: setLevel,
      onSpeechStart: () => {
        const { phase } = stateRef.current;
        if (phase === "speaking") {
          // The caller cut in: stop the agent mid-sentence.
          stopPlayback();
          dispatch({ type: "phase", phase: "recording" });
        } else if (phase === "listening") {
          dispatch({ type: "phase", phase: "recording" });
        }
      },
      onUtterance: (recording) => {
        if (busyRef.current) {
          queueRef.current = [...queueRef.current, { audio: recording.blob }].slice(-2);
          return;
        }
        void submitTurn({ audio: recording.blob });
      },
    });
    await listener.start();
    listenerRef.current = listener;
  }, [stopPlayback, submitTurn]);

  const endCall = React.useCallback(async () => {
    callTokenRef.current += 1;
    const listener = listenerRef.current;
    listenerRef.current = null;
    await Promise.all([listener?.stop(), recorderRef.current?.cancel()]);
    recorderRef.current = null;
    queueRef.current = [];
    busyRef.current = false;
    stopPlayback();
    setLevel(0);

    const sessionId = stateRef.current.sessionId;
    dispatch({ type: "end" });
    if (sessionId) {
      // Best effort: the caller has already hung up either way.
      await endSession(sessionId).catch(() => undefined);
    }
  }, [stopPlayback]);

  const startCall = React.useCallback(
    async (persona: PersonaOut) => {
      callTokenRef.current += 1;
      const token = callTokenRef.current;

      // Inside the click: unlock audio for the greeting, which lands later.
      const audio = audioRef.current ?? new Audio();
      audioRef.current = audio;
      const unlockUrl = silentWavUrl();
      audio.src = unlockUrl;
      void audio.play().catch(() => undefined);
      URL.revokeObjectURL(unlockUrl);

      dispatch({ type: "start", persona });

      // Ask for the microphone while the greeting is being made. If it is
      // refused the call carries on in tap-to-talk with the text box.
      const listening = beginListening().then(
        () => true,
        (error: unknown) => {
          if (token === callTokenRef.current) {
            dispatch({ type: "mode", mode: "manual", phase: "connecting" });
            dispatch({ type: "append", entry: { role: "system", text: describeError(error) } });
          }
          return false;
        },
      );

      try {
        const opened = await openCall(persona.id);
        if (token !== callTokenRef.current) return;
        await listening;
        if (token !== callTokenRef.current) return;
        dispatch({ type: "opened", turn: opened, caller: opened.caller ?? null });
        if (opened.outcome === "degraded_text_only") {
          dispatch({
            type: "append",
            entry: {
              role: "system",
              text: opened.degraded_reason ?? "Voice was unavailable this turn.",
            },
          });
        }
        await playReply(opened);
      } catch (error) {
        if (token !== callTokenRef.current) return;
        dispatch({ type: "error", message: describeError(error), phase: idlePhase() });
      }
    },
    [beginListening, idlePhase, playReply],
  );

  // -- Tap-to-talk ---------------------------------------------------------------
  const startRecording = React.useCallback(async () => {
    dispatch({ type: "clearError" });
    stopPlayback();
    const recorder = new Recorder({ onLevel: setLevel });
    try {
      await recorder.start();
    } catch (error) {
      dispatch({ type: "error", message: describeError(error), phase: "ready" });
      return;
    }
    recorderRef.current = recorder;
    dispatch({ type: "phase", phase: "recording" });
  }, [stopPlayback]);

  const stopRecordingAndSend = React.useCallback(async () => {
    const recorder = recorderRef.current;
    if (!recorder) return;
    recorderRef.current = null;

    const recording = await recorder.stop();
    setLevel(0);

    if (recording.durationSeconds < MIN_TURN_SECONDS) {
      dispatch({
        type: "error",
        message: "That was too short to catch. Hold the mic a moment longer.",
        phase: "ready",
      });
      return;
    }
    await submitTurn({ audio: recording.blob });
  }, [submitTurn]);

  const syncPaused = React.useCallback((muted: boolean) => {
    listenerRef.current?.setPaused(muted || typingRef.current);
  }, []);

  const toggleMute = React.useCallback(() => {
    const muted = !stateRef.current.muted;
    syncPaused(muted);
    dispatch({ type: "muted", muted });
  }, [syncPaused]);

  /** Called as the caller starts and stops typing. */
  const setTyping = React.useCallback(
    (typing: boolean) => {
      typingRef.current = typing;
      syncPaused(stateRef.current.muted);
    },
    [syncPaused],
  );

  /** The orb: mute in hands-free, start or send a recording in tap-to-talk. */
  const toggleMic = React.useCallback(async () => {
    const { mode, phase } = stateRef.current;
    if (mode === "handsfree") {
      toggleMute();
      return;
    }
    if (phase === "sending" || phase === "connecting") return;
    if (phase === "recording") await stopRecordingAndSend();
    else await startRecording();
  }, [startRecording, stopRecordingAndSend, toggleMute]);

  const setMode = React.useCallback(
    async (mode: CallMode) => {
      if (mode === stateRef.current.mode) return;
      if (mode === "manual") {
        const listener = listenerRef.current;
        listenerRef.current = null;
        await listener?.stop();
        setLevel(0);
        dispatch({ type: "mode", mode, phase: "ready" });
      } else {
        try {
          await beginListening();
          dispatch({ type: "mode", mode, phase: "listening" });
        } catch (error) {
          dispatch({ type: "error", message: describeError(error), phase: "ready" });
        }
      }
    },
    [beginListening],
  );

  /** Returns false when the message could not be taken yet (the call is still
   *  connecting), so the box can keep what was typed. */
  const sendText = React.useCallback(
    async (text: string): Promise<boolean> => {
      const trimmed = text.trim();
      if (!trimmed) return true;
      if (stateRef.current.phase === "connecting") return false;
      stopPlayback();
      if (busyRef.current) {
        // A reply is still on its way: send this straight after it.
        queueRef.current = [...queueRef.current, { text: trimmed }].slice(-2);
        return true;
      }
      await submitTurn({ text: trimmed });
      return true;
    },
    [stopPlayback, submitTurn],
  );

  // Release the mic and any object URL if the component unmounts mid-call.
  React.useEffect(
    () => () => {
      void listenerRef.current?.stop();
      void recorderRef.current?.cancel();
      stopPlayback();
    },
    [stopPlayback],
  );

  return {
    ...state,
    level,
    isActive,
    isBusy: state.phase === "sending" || state.phase === "connecting",
    startCall,
    endCall,
    toggleMic,
    toggleMute,
    setMode,
    setTyping,
    sendText,
    dismissError: React.useCallback(() => dispatch({ type: "clearError" }), []),
  };
}

