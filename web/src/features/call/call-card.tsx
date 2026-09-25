"use client";

import { Mic, MicOff, PhoneOff, Send } from "lucide-react";
import * as React from "react";
import { createPortal } from "react-dom";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { cn, formatClock, formatDuration } from "@/lib/utils";
import { Transcript } from "./transcript";
import type { useCall } from "./use-call";

type Call = ReturnType<typeof useCall>;

function phaseLabel(call: Call): string {
  if (call.mode === "handsfree" && call.muted) return "Muted — tap the mic to unmute";
  const handsFree = call.mode === "handsfree";
  switch (call.phase) {
    case "connecting":
      return "Connecting…";
    case "listening":
      return "Listening — just talk";
    case "recording":
      return handsFree ? "Hearing you…" : "Listening — tap to send";
    case "sending":
      return "Thinking…";
    case "speaking":
      return handsFree ? "Speaking — talk to interrupt" : "Speaking…";
    case "error":
      return "Tap the mic to try again";
    default:
      return handsFree ? "Listening — just talk" : "Tap the mic to talk";
  }
}

/**
 * The live call, as a single square card that zooms up out of the agent's
 * node on the arc rather than replacing the page.
 *
 * Before the first turn the mic circle sits alone in the centre. The first
 * tap lifts it to the top of the card, where it wobbles with the caller's
 * voice, and the conversation opens underneath it.
 */
export function CallCard({
  call,
  origin,
}: {
  call: Call;
  /** Viewport point the card zooms out of — the clicked agent's node. */
  origin: { x: number; y: number } | null;
}) {
  const { persona, phase } = call;
  const cardRef = React.useRef<HTMLDivElement | null>(null);
  const micRef = React.useRef<HTMLButtonElement | null>(null);
  const [closing, setClosing] = React.useState(false);
  const [touched, setTouched] = React.useState(false);

  // Aim the zoom at the node before first paint, so it grows out of the
  // circle that was clicked rather than out of the middle of the card.
  React.useLayoutEffect(() => {
    const el = cardRef.current;
    if (!el || !origin) return;
    const rect = el.getBoundingClientRect();
    el.style.transformOrigin = `${origin.x - rect.left}px ${origin.y - rect.top}px`;
  }, [origin]);

  const hangUp = React.useCallback(() => {
    if (closing) return;
    setClosing(true);
    window.setTimeout(() => void call.endCall(), 240);
  }, [call, closing]);

  // The latest hangUp, read from a ref so the effects below run once. `call` is a
  // new object on every render, and the timer renders this card every second:
  // depending on hangUp directly re-ran the effect each second and pulled focus
  // off the text box onto the mic button, so typing was impossible.
  const hangUpRef = React.useRef(hangUp);
  hangUpRef.current = hangUp;

  React.useEffect(() => {
    micRef.current?.focus();
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") hangUpRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previous;
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  if (!persona) return null;

  const isRecording = phase === "recording";
  const started = touched || call.entries.some((entry) => entry.role !== "system");
  const level = isRecording ? Math.min(1, call.level * 1.4) : 0;

  // Portalled to <body>: rendered inside <main> the overlay would be trapped
  // in its stacking context and slide under the sticky header.
  return createPortal(
    <div
      data-lenis-prevent
      className="fixed inset-0 z-[100] flex items-center justify-center p-3 sm:p-6"
      style={{ animation: closing ? undefined : "fade-in 0.3s ease-out" }}
    >
      <div
        aria-hidden
        className={cn(
          "absolute inset-0 bg-white/60 backdrop-blur-xl transition-opacity duration-300",
          closing && "opacity-0",
        )}
      />

      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-label={`Call with ${persona.display_name}`}
        data-agent={persona.id}
        className="relative flex h-[min(90dvh,38rem)] w-[min(94vw,38rem)] flex-col overflow-hidden rounded-[2rem] border border-white bg-white/90 shadow-[0_0_0_1px_rgba(var(--agent-accent-rgb),0.25),0_40px_90px_-30px_rgba(var(--agent-accent-rgb),0.55)]"
        style={{
          animation: `${closing ? "card-zoom-out 0.24s ease-in forwards" : "card-zoom-in 0.5s cubic-bezier(0.22,1,0.36,1)"}`,
        }}
      >
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-56"
          style={{
            background:
              "radial-gradient(60% 100% at 50% 0%, rgba(var(--agent-accent-rgb),0.16), transparent 100%)",
          }}
        />

        <header className="relative flex items-center justify-between gap-3 px-5 pt-4 pb-2 sm:px-6 sm:pt-5">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold text-ink">{persona.display_name}</h2>
            <p className="truncate font-mono text-xs text-agent">{persona.role}</p>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <span className="tabular font-mono text-sm text-ink-muted">
              {formatClock(call.elapsedSeconds)}
            </span>
            <button
              type="button"
              onClick={hangUp}
              className="inline-flex h-9 items-center gap-2 rounded-full border border-critical/40 bg-critical-soft px-3.5 text-xs font-medium text-critical transition-colors hover:bg-critical/15"
            >
              <PhoneOff className="size-3.5" aria-hidden />
              End call
            </button>
          </div>
        </header>

        <CallerNote call={call} />

        <div className="relative min-h-0 flex-1">
          {/* Before the first turn: the agent's pitch, under the centred mic. */}
          <div
            className={cn(
              "absolute inset-x-0 bottom-0 flex flex-col items-center gap-3 px-8 pb-2 text-center transition-opacity duration-500",
              started && "pointer-events-none opacity-0",
            )}
          >
            <p className="max-w-[44ch] text-sm text-ink-secondary">{persona.pitch}</p>
            {persona.tags.length ? (
              <ul className="flex flex-wrap justify-center gap-1.5">
                {persona.tags.map((tag) => (
                  <li key={tag}>
                    <Badge tone="neutral">{tag}</Badge>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>

          <div
            className="absolute left-1/2 z-10 flex flex-col items-center gap-2 transition-all duration-700 ease-[cubic-bezier(0.22,1,0.36,1)]"
            style={{
              top: started ? "0.25rem" : "42%",
              transform: `translate(-50%, ${started ? "0" : "-50%"})`,
            }}
          >
            <MicButton
              buttonRef={micRef}
              recording={isRecording}
              speaking={phase === "speaking"}
              muted={call.mode === "handsfree" && call.muted}
              handsFree={call.mode === "handsfree"}
              busy={call.isBusy}
              level={level}
              compact={started}
              onClick={() => {
                setTouched(true);
                void call.toggleMic();
              }}
            />
            <p className="tabular font-mono text-xs text-ink-muted">{phaseLabel(call)}</p>
          </div>

          <div
            className={cn(
              "absolute inset-x-0 top-[9.25rem] bottom-0 flex flex-col transition-opacity duration-500 sm:top-[9.75rem]",
              started ? "opacity-100 delay-300" : "pointer-events-none opacity-0",
            )}
          >
            {call.error ? (
              <div className="px-5 pb-2 sm:px-6">
                <Alert tone="critical" title="That turn didn't go through">
                  {call.error}
                </Alert>
              </div>
            ) : null}
            <Transcript
              entries={call.entries}
              className="min-h-0 flex-1 px-5 [mask-image:linear-gradient(to_bottom,transparent,#000_14px)] sm:px-6"
            />
          </div>
        </div>

        <footer className="relative flex items-center gap-3 border-t border-hairline px-5 py-3 sm:px-6">
          <TextFallback call={call} onSend={() => setTouched(true)} />
          {call.lastTurn ? (
            <Badge tone="neutral" className="hidden shrink-0 sm:inline-flex">
              {formatDuration(call.lastTurn.total_ms)}
            </Badge>
          ) : null}
        </footer>
      </div>
    </div>,
    document.body,
  );
}

function MicButton({
  buttonRef,
  recording,
  speaking,
  muted,
  handsFree,
  busy,
  level,
  compact,
  onClick,
}: {
  buttonRef: React.Ref<HTMLButtonElement>;
  recording: boolean;
  speaking: boolean;
  muted: boolean;
  handsFree: boolean;
  busy: boolean;
  level: number;
  compact: boolean;
  onClick: () => void;
}) {
  const live = recording || speaking;
  return (
    <button
      ref={buttonRef}
      type="button"
      onClick={onClick}
      disabled={busy && !speaking}
      aria-label={
        handsFree
          ? muted
            ? "Unmute microphone"
            : "Mute microphone"
          : recording
            ? "Stop recording and send"
            : "Start recording"
      }
      aria-pressed={handsFree ? muted : recording}
      className={cn(
        "relative flex items-center justify-center rounded-full focus-visible:outline-offset-4 transition-[width,height] duration-700 ease-[cubic-bezier(0.22,1,0.36,1)]",
        "disabled:cursor-not-allowed disabled:opacity-60",
        compact ? "size-24" : "size-40",
      )}
    >
      {live ? (
        <>
          <span
            aria-hidden
            className="absolute inset-[18%] rounded-full border border-agent/50"
            style={{ animation: "ring-out 1.8s ease-out infinite" }}
          />
          <span
            aria-hidden
            className="absolute inset-[18%] rounded-full border border-agent/40"
            style={{ animation: "ring-out 1.8s ease-out 0.9s infinite" }}
          />
        </>
      ) : null}

      <span
        aria-hidden
        className="absolute inset-0 rounded-full border border-agent/25"
        style={{ transform: `scale(${1 + level * 0.12})`, transition: "transform 80ms linear" }}
      />

      <span
        aria-hidden
        className="absolute inset-[14%] bg-agent-soft"
        style={{
          animation: `blob-morph ${live ? 3.2 : 9}s ease-in-out infinite`,
          transform: `scale(${1 + level * 0.32})`,
          transition: "transform 80ms linear",
          boxShadow: "0 0 0 1px rgba(var(--agent-accent-rgb),0.3)",
        }}
      />

      <span
        aria-hidden
        className="absolute inset-[30%] bg-agent"
        style={{
          animation: `blob-morph ${live ? 2.4 : 7}s ease-in-out infinite reverse`,
          transform: `scale(${1 + level * 0.5})`,
          transition: "transform 80ms linear",
          boxShadow: "0 12px 30px -8px rgba(var(--agent-accent-rgb),0.6)",
        }}
      />

      {muted ? (
        <MicOff
          aria-hidden
          className={cn(
            "relative text-white transition-all duration-700",
            compact ? "size-6" : "size-9",
          )}
        />
      ) : (
        <Mic
          aria-hidden
          className={cn(
            "relative text-white transition-all duration-700",
            compact ? "size-6" : "size-9",
          )}
        />
      )}
    </button>
  );
}

/** Who the visitor is on this call, and a line they could open with. The caller is
 *  assigned at random, so without this they would not know what to talk about. */
function CallerNote({ call }: { call: Call }) {
  const handsFree = call.mode === "handsfree";
  const { caller } = call;
  return (
    <div className="relative flex items-center justify-between gap-3 px-5 pb-2 sm:px-6">
      <p className="min-w-0 truncate text-xs text-ink-secondary">
        {caller ? (
          <>
            You&apos;re calling as <span className="font-medium text-ink">{caller.name}</span>
            {" · "}
            try <span className="italic">&ldquo;{caller.suggestion}&rdquo;</span>
          </>
        ) : call.phase === "connecting" ? (
          "Connecting…"
        ) : (
          "You're a new enquirer. Tell the agent what you're looking for."
        )}
      </p>
      <button
        type="button"
        onClick={() => void call.setMode(handsFree ? "manual" : "handsfree")}
        className="shrink-0 rounded-full border border-hairline px-2.5 py-1 text-[11px] font-medium text-ink-secondary transition-colors hover:border-agent hover:text-agent"
      >
        {handsFree ? "Switch to tap-to-talk" : "Switch to hands-free"}
      </button>
    </div>
  );
}

function TextFallback({ call, onSend }: { call: Call; onSend: () => void }) {
  const [text, setText] = React.useState("");
  const connecting = call.phase === "connecting";

  // Typing never locks the box. A message written while the agent is still
  // answering is queued and sent right after; only while the call is still
  // connecting is it held back, and then the text stays where it is.
  const submit = async () => {
    if (!text.trim() || connecting) return;
    const pending = text;
    setText("");
    onSend();
    if (!(await call.sendText(pending))) setText(pending);
  };

  return (
    <form
      className="flex min-w-0 flex-1 items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <input
        value={text}
        onChange={(event) => setText(event.target.value)}
        onFocus={() => call.setTyping(true)}
        onBlur={() => call.setTyping(false)}
        placeholder={connecting ? "Connecting… you can type meanwhile" : "Or type instead of talking…"}
        aria-label="Type a message instead of talking"
        autoComplete="off"
        className="h-10 min-w-0 flex-1 rounded-full border border-hairline bg-surface-sunken px-4 text-sm text-ink placeholder:text-ink-muted focus:border-agent focus:outline-none"
      />
      <button
        type="submit"
        disabled={!text.trim() || connecting}
        className="flex size-10 shrink-0 items-center justify-center rounded-full bg-agent text-white transition-[filter,opacity] hover:brightness-110 disabled:opacity-40"
      >
        <Send className="size-4" aria-hidden />
        <span className="sr-only">Send message</span>
      </button>
    </form>
  );
}
