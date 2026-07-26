"use client";

import { Maximize2, Minimize2, Ratio, Scan } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type ConnectionState =
  | "connecting"
  | "connected"
  | "closed"
  | "denied"
  | "error";

/**
 * How the fixed-size agent framebuffer (1280x800, see images/vnc) is fitted
 * into the viewer pane:
 *  - "fit"    scales the framebuffer to the pane, aspect-preserved (default —
 *             keeps the whole session visible on small/laptop viewports).
 *  - "actual" renders 1:1 pixels; the pane scrolls if the framebuffer is
 *             larger than the pane (crisp text at native resolution).
 */
type ViewMode = "fit" | "actual";

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
  const wrapperRef = React.useRef<HTMLDivElement | null>(null);
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const rfbRef = React.useRef<import("@novnc/novnc").default | null>(null);
  const [state, setState] = React.useState<ConnectionState>("connecting");
  const [errorMessage, setErrorMessage] = React.useState<string | null>(null);
  const [viewMode, setViewMode] = React.useState<ViewMode>("fit");
  const [isFullscreen, setIsFullscreen] = React.useState(false);
  // Read the current mode inside the connect effect without making that effect
  // depend on it (a mode change must not tear down and reconnect the session).
  const viewModeRef = React.useRef(viewMode);
  React.useEffect(() => {
    viewModeRef.current = viewMode;
  }, [viewMode]);

  // Push a view mode onto a live RFB client. "fit" scales the framebuffer to the
  // pane; "actual" turns scaling off so noVNC draws native pixels and the pane
  // (overflow-auto) scrolls.
  const applyViewMode = React.useCallback(
    (client: import("@novnc/novnc").default, mode: ViewMode) => {
      client.scaleViewport = mode === "fit";
    },
    []
  );

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
      applyViewMode(client, viewModeRef.current);
      client.background = "#0b0f17";
      rfb = client;
      rfbRef.current = client;

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
      rfbRef.current = null;
    };
  }, [vncPath, buildWsUrl, applyViewMode]);

  // Live-apply a mode change to the running client (no reconnect).
  React.useEffect(() => {
    if (rfbRef.current) applyViewMode(rfbRef.current, viewMode);
  }, [viewMode, applyViewMode]);

  // Track fullscreen so the toggle button reflects the real document state
  // (Esc exits fullscreen without going through our handler).
  React.useEffect(() => {
    const onChange = () =>
      setIsFullscreen(document.fullscreenElement === wrapperRef.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const toggleFullscreen = React.useCallback(() => {
    const el = wrapperRef.current;
    if (!el) return;
    if (document.fullscreenElement === el) {
      void document.exitFullscreen?.();
    } else {
      void el.requestFullscreen?.().catch(() => {
        // fullscreen can be blocked by permissions policy; leave state as-is
      });
    }
  }, []);

  const canControl = state === "connected";

  return (
    <div ref={wrapperRef} className={cn("flex flex-col gap-2 bg-inherit", className)}>
      <div className="flex items-center justify-between gap-2">
        <ConnectionBanner state={state} message={errorMessage} />
        <div className="ml-auto flex shrink-0 items-center gap-1">
          <Button
            type="button"
            size="icon-sm"
            variant={viewMode === "fit" ? "secondary" : "ghost"}
            aria-pressed={viewMode === "fit"}
            disabled={!canControl}
            title="Fit to window"
            aria-label="Fit session to window"
            onClick={() => setViewMode("fit")}
          >
            <Scan />
          </Button>
          <Button
            type="button"
            size="icon-sm"
            variant={viewMode === "actual" ? "secondary" : "ghost"}
            aria-pressed={viewMode === "actual"}
            disabled={!canControl}
            title="Actual size (1:1)"
            aria-label="Show session at actual size"
            onClick={() => setViewMode("actual")}
          >
            <Ratio />
          </Button>
          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            title={isFullscreen ? "Exit fullscreen" : "Fullscreen"}
            aria-label={isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
            onClick={toggleFullscreen}
          >
            {isFullscreen ? <Minimize2 /> : <Maximize2 />}
          </Button>
        </div>
      </div>
      <div
        ref={containerRef}
        role="region"
        aria-label="Live agent session"
        // exact VNC-canvas background — must match the noVNC client.background
        // literal set above; not tokenizable.
        // eslint-disable-next-line astrolift/no-raw-design-values
        className={cn(
          "min-h-[24rem] flex-1 rounded-md border bg-[#0b0f17]",
          // actual size scrolls native pixels; fit scales within the pane.
          viewMode === "actual" ? "overflow-auto" : "overflow-hidden"
        )}
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
      ? "border-danger-border bg-danger/10 text-danger-fg"
      : tone === "warn"
        ? "border-warning-border bg-warning/10 text-warning-fg"
        : "border-info-border bg-info/10 text-info-fg";

  return (
    <div className={cn("rounded-md border px-2.5 py-1.5 text-xs", cls)} role="status">
      {label}
    </div>
  );
}
