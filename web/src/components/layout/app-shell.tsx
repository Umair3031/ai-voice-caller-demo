"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";

import { SmoothScroll } from "@/components/motion/smooth-scroll";
import { PageFlakes } from "@/components/graphics/page-flakes";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Live Line" },
  { href: "/console", label: "Operator Console" },
] as const;

/**
 * The frame both routes render inside.
 *
 * Identical brand and navigation on every page — the customer line and the
 * operator console are one product, not two apps stitched together with an
 * iframe. The ambient flake layer sits behind everything so the whole site
 * shares one continuous backdrop rather than a stack of separate pages.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="flex min-h-dvh flex-col overflow-x-clip bg-surface-page">
      <SmoothScroll />
      <PageFlakes />

      <header className="sticky top-0 z-30 border-b border-hairline bg-surface-page/95 sm:bg-surface-page/80 sm:backdrop-blur-md">
        <div className="mx-auto flex h-16 w-full max-w-[1280px] items-center gap-6 px-4 sm:px-6">
          {/* shrink-0 keeps the logo's own box from ever competing with the
              nav pill for space; growing it here can't change the pill's
              width since they're independent flex items, not flex-1. */}
          <Link href="/" className="flex shrink-0 items-center gap-2.5">
            <Image src="/novaconnect-logo.png" alt="NovaConnect" width={48} height={48} className="size-12" priority />
            <span className="hidden text-xs text-ink-muted sm:inline">Telecoms</span>
          </Link>

          <nav
            aria-label="Primary"
            className="flex min-w-0 items-center gap-1 rounded-full bg-surface-sunken p-1"
          >
            {NAV.map(({ href, label }) => {
              const active = pathname === href;
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "rounded-full px-3.5 py-1.5 text-sm font-medium whitespace-nowrap transition-all",
                    active
                      ? "bg-surface text-ink shadow-(--shadow-card)"
                      : "text-ink-muted hover:text-ink",
                  )}
                >
                  {label}
                </Link>
              );
            })}
          </nav>
        </div>
      </header>

      <main className="relative z-10 mx-auto w-full max-w-[1280px] flex-1 px-4 py-6 sm:px-6 sm:py-8">
        {children}
      </main>
    </div>
  );
}
