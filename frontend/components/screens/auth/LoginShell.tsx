import type * as React from "react";

/** The (login) group's frame: one centred card on the page background. */
export function LoginShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-background flex min-h-screen items-center justify-center">{children}</div>
  );
}
