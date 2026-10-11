import Image from "next/image";
import type * as React from "react";

/**
 * The (login) group's frame: the Astrolift brand bar over one centred card on
 * the page background. The CLI device pages and the edge sign-in use the same
 * bar, so every sign-in surface reads as Astrolift (#2285).
 */
export function LoginShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-background flex min-h-screen flex-col">
      <header className="bg-sidebar text-sidebar-foreground border-sidebar-border flex items-center gap-2 border-b px-5 py-3">
        <Image src="/logo.svg" alt="" width={22} height={22} priority />
        <span className="font-head text-sm font-semibold tracking-tight">Astrolift</span>
      </header>
      <main className="flex flex-1 items-center justify-center p-4">{children}</main>
    </div>
  );
}
