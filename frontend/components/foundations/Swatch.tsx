"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * One design token as a swatch: the colour itself, its name, and the value
 * it resolves to under the current ground and accent. Reads the computed
 * value, so switching the Storybook toolbar shows what the app would paint.
 */
export function Swatch({ token, className }: { token: string; className?: string }) {
  const ref = React.useRef<HTMLDivElement>(null);
  const [value, setValue] = React.useState("");

  React.useEffect(() => {
    const read = () => {
      if (ref.current) setValue(getComputedStyle(ref.current).backgroundColor);
    };
    read();
    // The toolbar restamps <html>'s attributes; follow it.
    const observer = new MutationObserver(read);
    observer.observe(document.documentElement, { attributes: true });
    return () => observer.disconnect();
  }, [token]);

  return (
    <div className={cn("flex min-w-0 items-center gap-3", className)}>
      <div
        ref={ref}
        className="border-border size-10 shrink-0 rounded-md border"
        style={{ background: `var(--${token})` }}
      />
      <div className="min-w-0 text-xs">
        <div className="text-foreground truncate font-mono">--{token}</div>
        <div className="text-muted-foreground truncate font-mono">{value}</div>
      </div>
    </div>
  );
}

/** A labelled group of swatches. */
export function SwatchGroup({ title, tokens }: { title: string; tokens: string[] }) {
  return (
    <section className="min-w-0">
      <h3 className="text-muted-foreground mb-3 text-xs font-semibold tracking-wider uppercase">
        {title}
      </h3>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(14rem,1fr))] gap-4">
        {tokens.map((t) => (
          <Swatch key={t} token={t} />
        ))}
      </div>
    </section>
  );
}
