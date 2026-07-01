"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

type ConnectionState =
  | "connecting"
  | "connected"
  | "closed"
  | "denied"
  | "error";

export interface VncViewerProps {
  /**
   * Relay path published on the task as ``vnc_url`` — e.g.
   * ``/app/vnc/<task-guid>`` (see backend/core/schema/vnc_ws.py).
   * The ws(s) URL is derived from it client-side, mirroring
   * TerminalEmulator.buildWsUrl.
   */
  vncPath: string;
  className?: string;
}

/**
 * Live noVNC framebuffer for a RUNNING agent task.
 *
 * Connects the @novnc/novnc RFB client to the backend VNC relay
 * (``/app/vnc/<guid>``) over a websocket and renders the agent's
 * desktop into a canvas. The relay handles auth via the session
 * cookie (4401/4403 close codes), so no credentials are forwarded.
 */
export function VncViewer({ vncPath, className }: VncViewerProps) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const [state, setState] = React.useState<ConnectionState>("connecting");
  const [errorMessage, setErrorMessage] = React.useState<string | null>(null);

  const buildWsUrl = React.useCallback((): string => {
    const wsOrigin = process.env.NEXT_PUBLIC_WS_ORIGIN;
    const path = vncPath.startsWith("/") ? vncPath : `/${vncPath}`;
    if (wsOrigin) {
      return `${wsOrigin.replace(/\/$/, "")}${path}`;
    }
    if (typeof window === "undefined") return path;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}${path}`;
  }, [vncPath]);

  React.useEffect(() => {
    const target = containerRef.current;
    if (!target || !vncPath) return;

    let disposed = false;
    // RFB instance type is loaded dynamically so the noVNC bundle
    // stays out of the server render path (it touches window/document
    // at import time).
    let rfb: import("@novnc/novnc").default | null = null;

    setState("connecting");
    setErrorMessage(null);

    void import("@novnc/novnc").then(({ default: RFB }) => {
      if (disposed || !containerRef.current) return;
      let client: InstanceType<typeof RFB>;
      try {
        client = new RFB(containerRef.current, buildWsUrl());
      } catch (err) {
        setState("error");
        setErrorMessage(err instanceof Error ? err.message : String(err));
        return;
      }
      client.scaleViewport = true;
      client.background = "#0b0f17";
      rfb = client;

      client.addEventListener("connect", () => {
        if (disposed) return;
        setState("connected");
      });

      client.addEventListener("disconnect", (e: Event) => {
        if (disposed) return;
        const detail = (e as CustomEvent<{ clean?: boolean }>).detail;
        setState(detail?.clean ? "closed" : "error");
      });

      // The relay denies via a WS close (4401/4403). noVNC surfaces
      // a non-clean disconnect; security failures arrive here too.
      client.addEventListener("securityfailure", (e: Event) => {
        if (disposed) return;
        const detail = (e as CustomEvent<{ reason?: string }>).detail;
        setState("denied");
        setErrorMessage(detail?.reason ?? "Access denied.");
      });
    });

    return () => {
      disposed = true;
      if (rfb) {
        try {
          rfb.disconnect();
        } catch {
          // already torn down
        }
        rfb = null;
      }
    };
  }, [vncPath, buildWsUrl]);

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <ConnectionBanner state={state} message={errorMessage} />
      <div
        ref={containerRef}
        role="region"
        aria-label="Live agent session"
        // exact VNC-canvas background — must match the noVNC client.background
        // literal set above; not tokenizable.
        // eslint-disable-next-line astrolift/no-raw-design-values
        className="min-h-[24rem] flex-1 overflow-hidden rounded-md border bg-[#0b0f17]"
      />
    </div>
  );
}

function ConnectionBanner({
  state,
  message,
}: {
  state: ConnectionState;
  message: string | null;
}) {
  if (state === "connected") return null;

  let tone: "info" | "warn" | "error" = "info";
  let label: string;
  switch (state) {
    case "connecting":
      label = "Connecting to live session…";
      break;
    case "closed":
      label = "Session closed.";
      tone = "warn";
      break;
    case "denied":
      label = message ?? "Access denied.";
      tone = "error";
      break;
    case "error":
      label = message ?? "Connection error.";
      tone = "error";
      break;
    default:
      label = "";
  }

  const cls =
    tone === "error"
      ? "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300"
      : tone === "warn"
        ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300"
        : "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300";

  return (
    <div className={cn("rounded-md border px-2.5 py-1.5 text-xs", cls)} role="status">
      {label}
    </div>
  );
}
