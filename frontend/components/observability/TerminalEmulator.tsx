"use client";

import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { Terminal } from "@xterm/xterm";
import { useTranslations } from "next-intl";
import * as React from "react";

import "@xterm/xterm/css/xterm.css";

import { cn } from "@/lib/utils";

/** Server-frame protocol — matches backend/core/schema/exec_ws.py.  */
type ServerFrame =
  | { type: "stdout"; data: string }
  | { type: "stderr"; data: string }
  | { type: "exit"; code: number }
  | { type: "error"; message: string }
  | { type: "ready" }
  | {
      type: "replay";
      lines: (
        | { type: "stdout"; data: string }
        | { type: "stderr"; data: string }
        | { type: "exit"; code: number }
      )[];
    };

type ConnectionState =
  | "idle"
  | "connecting"
  | "ready"
  | "reconnecting"
  | "closed"
  | "denied"
  | "error";

const MAX_BACKOFF_MS = 30_000;
const INITIAL_BACKOFF_MS = 500;

export interface TerminalEmulatorProps {
  appSlug: string;
  /** Pod name — encoded into the WS path. */
  podName: string;
  /** Container name — passed in the open frame. */
  container: string;
  /** Command (defaults to ``["sh"]`` server-side if empty). */
  command?: string[];
  className?: string;
}

/**
 * Browser PTY into ``kubectl exec -it <pod> -c <container> -- <command>``.
 *
 * Connects to the backend's ``/app/exec/<app>/<pod>`` WebSocket
 * (see ``backend/core/schema/exec_ws.py``), opens an exec session,
 * and renders the bidirectional stream through an xterm.js terminal.
 *
 * Reconnect: the WS drops trigger an exponential-backoff reconnect
 * (up to 30 s). On the new connection we send a ``replay`` frame so
 * the server-side ring buffer catches the terminal up before
 * resuming live IO. Typed input that landed *during* the dead window
 * is queued locally and flushed once the new session is ``ready``.
 */
export function TerminalEmulator(props: TerminalEmulatorProps) {
  const { appSlug, podName, container, command, className } = props;
  const t = useTranslations("apps.shell.terminal");

  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const termRef = React.useRef<Terminal | null>(null);
  const fitRef = React.useRef<FitAddon | null>(null);
  const wsRef = React.useRef<WebSocket | null>(null);
  const stdinBufferRef = React.useRef<string[]>([]);
  const reconnectTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const backoffRef = React.useRef<number>(INITIAL_BACKOFF_MS);
  const disposedRef = React.useRef<boolean>(false);

  const [state, setState] = React.useState<ConnectionState>("idle");
  const [errorMessage, setErrorMessage] = React.useState<string | null>(null);

  // Lifetime: one xterm per mount, reused across reconnects.
  React.useEffect(() => {
    if (!containerRef.current) return;
    const term = new Terminal({
      cursorBlink: true,
      convertEol: true,
      fontFamily:
        'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace',
      fontSize: 13,
      // The kubelet exec channel ignores mouse-tracking sequences,
      // and consumer terminal apps (htop, vim) tend to confuse the
      // reader when mouse capture is on — disable to keep paste +
      // scroll predictable.
      disableStdin: false,
      allowProposedApi: true,
      theme: {
        background: "#0b0f17",
        foreground: "#e2e8f0",
        cursor: "#7dd3fc",
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon());
    term.open(containerRef.current);
    // Defer the first fit so the parent has its real size.
    requestAnimationFrame(() => {
      try {
        fit.fit();
      } catch {
        // jsdom or zero-size parents — best-effort
      }
    });
    termRef.current = term;
    fitRef.current = fit;
    return () => {
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, []);

  // Resize-observer → propagate to the PTY when the WS is open.
  React.useEffect(() => {
    if (!containerRef.current) return;
    const ob = new ResizeObserver(() => {
      const fit = fitRef.current;
      const term = termRef.current;
      if (!fit || !term) return;
      try {
        fit.fit();
      } catch {
        return;
      }
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({
            type: "resize",
            rows: term.rows,
            cols: term.cols,
          })
        );
      }
    });
    ob.observe(containerRef.current);
    return () => ob.disconnect();
  }, []);

  const commandKey = JSON.stringify(command ?? []);
  const authExpiredLabel = t("authExpired");

  const buildWsUrl = React.useCallback((): string => {
    const wsOrigin = process.env.NEXT_PUBLIC_WS_ORIGIN;
    const path = `/app/exec/${encodeURIComponent(appSlug)}/${encodeURIComponent(podName)}`;
    if (wsOrigin) {
      return `${wsOrigin.replace(/\/$/, "")}${path}`;
    }
    if (typeof window === "undefined") return path;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}${path}`;
  }, [appSlug, podName]);

  const flushStdinBuffer = React.useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const buffered = stdinBufferRef.current;
    if (buffered.length === 0) return;
    for (const chunk of buffered) {
      ws.send(JSON.stringify({ type: "stdin", data: chunk }));
    }
    stdinBufferRef.current = [];
  }, []);

  const connectRef = React.useRef<(() => void) | null>(null);

  const scheduleReconnect = React.useCallback(() => {
    if (disposedRef.current) return;
    setState("reconnecting");
    const delay = backoffRef.current;
    backoffRef.current = Math.min(delay * 2, MAX_BACKOFF_MS);
    if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    // The actual reconnect runs through the ref so we don't have a
    // useCallback → useCallback cycle on `connect`.
    reconnectTimerRef.current = setTimeout(() => connectRef.current?.(), delay);
  }, []);

  const connect = React.useCallback(() => {
    if (disposedRef.current) return;
    const term = termRef.current;
    if (!term) return;

    setState((prev) => (prev === "reconnecting" ? "reconnecting" : "connecting"));
    setErrorMessage(null);

    let ws: WebSocket;
    try {
      ws = new WebSocket(buildWsUrl());
    } catch (err) {
      setErrorMessage((err as Error).message);
      scheduleReconnect();
      return;
    }
    wsRef.current = ws;

    // Forward terminal keystrokes to the WS — when the WS is mid-reconnect
    // we queue them locally so the operator's input isn't lost.
    const dataDisposable = term.onData((data: string) => {
      const live = wsRef.current;
      if (live && live.readyState === WebSocket.OPEN) {
        live.send(JSON.stringify({ type: "stdin", data }));
      } else {
        stdinBufferRef.current.push(data);
      }
    });
    ws.addEventListener("close", () => dataDisposable.dispose());

    const isReconnect = backoffRef.current > INITIAL_BACKOFF_MS;

    ws.onopen = () => {
      if (disposedRef.current) {
        ws.close();
        return;
      }
      ws.send(
        JSON.stringify({
          type: "open",
          container,
          command: command ?? [],
        })
      );
      ws.send(
        JSON.stringify({
          type: "resize",
          rows: term.rows,
          cols: term.cols,
        })
      );
      if (isReconnect) {
        // Drain the server-side ring buffer before the new live IO
        // starts arriving so the operator sees the missed output.
        ws.send(JSON.stringify({ type: "replay" }));
      }
    };

    ws.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      let frame: ServerFrame;
      try {
        frame = JSON.parse(event.data);
      } catch {
        return;
      }
      switch (frame.type) {
        case "ready":
          setState("ready");
          backoffRef.current = INITIAL_BACKOFF_MS;
          flushStdinBuffer();
          return;
        case "stdout":
          term.write(frame.data);
          return;
        case "stderr":
          // Render stderr in the same stream — terminal apps already
          // emit their own colors and the kubelet doesn't split
          // visually-distinct channels.
          term.write(frame.data);
          return;
        case "exit":
          term.writeln(`\r\n\x1b[2m[process exited with code ${frame.code}]\x1b[0m`);
          setState("closed");
          return;
        case "error":
          term.writeln(`\r\n\x1b[31m[error] ${frame.message}\x1b[0m`);
          setErrorMessage(frame.message);
          return;
        case "replay":
          for (const line of frame.lines) {
            if (line.type === "stdout" || line.type === "stderr") {
              term.write(line.data);
            }
          }
          return;
      }
    };

    ws.onerror = () => {
      // The WS spec strips error detail; the close handler runs
      // straight after with the real reason, so we just flag state
      // here and let `onclose` decide whether to reconnect.
      setState((prev) => (prev === "ready" ? "reconnecting" : prev));
    };

    ws.onclose = (event) => {
      if (disposedRef.current) return;
      // 4403 = permission denied. Don't reconnect — the operator
      // can't acquire the capability by retrying.
      if (event.code === 4403) {
        setState("denied");
        return;
      }
      if (event.code === 4401) {
        setState("denied");
        setErrorMessage(authExpiredLabel);
        return;
      }
      // Clean exit (1000) on session end → leave the terminal in
      // a closed state and let the operator re-open manually.
      if (event.code === 1000 && backoffRef.current === INITIAL_BACKOFF_MS) {
        setState("closed");
        return;
      }
      scheduleReconnect();
    };
  }, [buildWsUrl, container, command, flushStdinBuffer, scheduleReconnect, authExpiredLabel]);

  React.useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  // Connect / reconnect — owns the WS lifecycle. Re-runs when the
  // target pod / container / command tuple changes.
  React.useEffect(() => {
    disposedRef.current = false;
    backoffRef.current = INITIAL_BACKOFF_MS;
    if (!podName || !container) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setState("idle");
      return;
    }
    connect();
    return () => {
      disposedRef.current = true;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(JSON.stringify({ type: "close" }));
        } catch {
          // ws may have just transitioned to CLOSING
        }
        ws.close(1000);
      }
      wsRef.current = null;
    };
  }, [appSlug, podName, container, commandKey, connect]);

  return (
    // The height must be *definite*. An auto-height host closes a feedback
    // loop with the fit addon: fit() sizes `rows` to the host, xterm renders
    // those rows, the auto-height host grows to fit them, the ResizeObserver
    // fires, fit() adds more rows — and the terminal walks down the page
    // forever instead of scrolling inside itself (#1245). A bounded host makes
    // fit() converge on the first pass and lets .xterm-viewport do the
    // scrolling. Callers may override the default height via `className`.
    <div className={cn("flex h-96 min-h-0 flex-col gap-2", className)}>
      <ConnectionBanner state={state} message={errorMessage} />
      <div
        ref={containerRef}
        role="region"
        aria-label={t("ariaLabel")}
        // exact terminal-canvas background — must match the xterm
        // theme.background literal set above; not tokenizable.
        // eslint-disable-next-line astrolift/no-raw-design-values
        className="min-h-0 flex-1 rounded-md border bg-[#0b0f17] p-2 [&_.xterm-viewport]:!overflow-y-auto"
      />
    </div>
  );
}

function ConnectionBanner({ state, message }: { state: ConnectionState; message: string | null }) {
  const t = useTranslations("apps.shell.terminal");
  if (state === "ready" || state === "idle") return null;

  let tone: "info" | "warn" | "error" = "info";
  let label: string;
  switch (state) {
    case "connecting":
      label = t("statusConnecting");
      break;
    case "reconnecting":
      label = t("statusReconnecting");
      tone = "warn";
      break;
    case "closed":
      label = t("statusClosed");
      break;
    case "denied":
      label = message ?? t("statusDenied");
      tone = "error";
      break;
    case "error":
      label = message ?? t("statusError");
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
