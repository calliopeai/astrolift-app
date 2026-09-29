"use client";

// Raw hex + inline styles are intentional (astrolift/no-raw-design-values
// escape-hatch case): this boundary replaces the root layout on hard
// crashes, where globals.css — and therefore every design token — may not
// be loaded, so Tailwind classes and var(--token) cannot apply.

export interface GlobalErrorScreenProps {
  /** The thrown error's message; empty falls back to a generic line. */
  message: string;
  /** Next's server-error digest, shown so a report can be traced. */
  digest?: string;
  onReset: () => void;
}

/** The body of app/global-error.tsx; the route keeps the <html>/<body> it must render. */
export function GlobalErrorScreen({ message, digest, onReset }: GlobalErrorScreenProps) {
  return (
    <div
      style={{
        display: "flex",
        minHeight: "100vh",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: "1rem",
        padding: "1.5rem",
        textAlign: "center",
        fontFamily: "system-ui, sans-serif",
      }}
    >
      <h2 style={{ fontSize: "1.125rem", fontWeight: 600, margin: 0 }}>Something went wrong</h2>
      <p style={{ fontSize: "0.875rem", color: "#6b7280", margin: 0, maxWidth: "24rem" }}>
        {message || "A critical error occurred. Please refresh the page."}
      </p>
      {digest && (
        <p style={{ fontSize: "0.75rem", color: "#9ca3af", fontFamily: "monospace", margin: 0 }}>
          Error ID: {digest}
        </p>
      )}
      <button
        onClick={onReset}
        style={{
          padding: "0.375rem 1rem",
          fontSize: "0.875rem",
          border: "1px solid #d1d5db",
          borderRadius: "0.375rem",
          background: "transparent",
          cursor: "pointer",
        }}
      >
        Try again
      </button>
    </div>
  );
}
