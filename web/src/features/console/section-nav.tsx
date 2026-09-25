"use client";

import * as React from "react";

import { scrollToId } from "@/components/motion/smooth-scroll";
import { cn } from "@/lib/utils";

export interface ConsoleSection {
  id: string;
  label: string;
}

/** Height of the sticky app header plus this bar's own breathing room. */
const SCROLL_OFFSET = 128;

/**
 * A sticky in-page tab bar with scroll-spy: the highlighted tab follows
 * whichever section is under the reading line, and clicking one glides there.
 * Scrolls horizontally on narrow screens rather than wrapping.
 */
export function SectionNav({ sections }: { sections: ConsoleSection[] }) {
  const [active, setActive] = React.useState(sections[0]?.id ?? "");
  const barRef = React.useRef<HTMLElement | null>(null);

  // The active section is the last one whose top has crossed the reading
  // line just under the sticky chrome (or the last one, at the page bottom).
  React.useEffect(() => {
    let frame = 0;
    const update = () => {
      frame = 0;
      const atBottom =
        window.innerHeight + window.scrollY >=
        document.documentElement.scrollHeight - 4;
      let current = sections[0]?.id ?? "";
      for (const section of sections) {
        const el = document.getElementById(section.id);
        if (el && el.getBoundingClientRect().top <= SCROLL_OFFSET + 24)
          current = section.id;
      }
      setActive(
        atBottom ? (sections[sections.length - 1]?.id ?? current) : current,
      );
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    return () => {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [sections]);

  // Keep the active tab in view when the bar itself is scrolled sideways.
  React.useEffect(() => {
    const bar = barRef.current;
    const tab = bar?.querySelector<HTMLElement>('[aria-current="true"]');
    if (!bar || !tab) return;
    bar.scrollTo({
      left: tab.offsetLeft - (bar.clientWidth - tab.clientWidth) / 2,
      behavior: "smooth",
    });
  }, [active]);

  return (
    <div className="sticky top-16 z-20 mr-[calc(50%-50vw)] ml-[calc(50%-50vw)] border-b border-hairline bg-surface-page/80 backdrop-blur-md">
      <nav
        ref={barRef}
        aria-label="Console sections"
        className="mx-auto flex max-w-[1280px] gap-1 overflow-x-auto px-4 py-2 sm:px-6 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {sections.map((section) => {
          const current = section.id === active;
          return (
            <button
              key={section.id}
              type="button"
              aria-current={current ? "true" : undefined}
              onClick={() => {
                setActive(section.id);
                scrollToId(section.id, {
                  align: "start",
                  offset: SCROLL_OFFSET,
                });
              }}
              className={cn(
                "shrink-0 rounded-full px-3.5 py-1.5 text-sm font-medium whitespace-nowrap transition-colors",
                current
                  ? "bg-brand text-brand-ink shadow-(--shadow-card)"
                  : "text-ink-muted hover:bg-surface-sunken hover:text-ink",
              )}
            >
              {section.label}
            </button>
          );
        })}
      </nav>
    </div>
  );
}
