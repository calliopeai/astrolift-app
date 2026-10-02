"use client";

/**
 * LogView: the one log pane (spec 44 §5.5, §7). Monospaced lines with a UTC
 * time and a level, inside a Panel with Follow and Download in its heading.
 *
 *   <LogView lines={lines} onDownload={download} loading={loading && !data} error={error?.message} onRetry={refetch} />
 *
 * It follows the end by default and stops when the reader scrolls up; the
 * Follow toggle or "Jump to latest" picks it up again (see use-follow.ts).
 * A finished run reads the same: it opens at the end, where a failure is.
 * Long unbroken lines wrap. Lines render in fixed chunks that the browser
 * skips laying out while off screen, so 5000 lines stay cheap without
 * virtualising. Level comes from the line, or is classified from the text
 * with the observability LogViewer's `classifyLogLevel`.
 */

import { ArrowDownToLineIcon, DownloadIcon, ScrollTextIcon } from "lucide-react";
import * as React from "react";
import { useTranslations } from "next-intl";

import { classifyLogLevel, type LogLevel } from "@/components/observability/LogViewer";
import { Panel } from "@/components/panel/Panel";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { formatLogTime } from "./format";
import { useFollow } from "./use-follow";

export type { LogLevel };

export interface LogLine {
  /** ISO instant or epoch ms. */
  ts: string | number;
  message: string;
  /** Classified from the message when absent. */
  level?: LogLevel;
}

export interface LogViewProps {
  lines: LogLine[];
  /** Panel title. Defaults to "Log". */
  title?: string;
  /** Hidden when absent. The caller builds the file (it may hold more than the pane). */
  onDownload?: () => void;
  loading?: boolean;
  error?: string | { message: string } | null;
  onRetry?: () => void;
  /** Said inside the pane while there are no lines. */
  emptyHint?: React.ReactNode;
  /** More controls in the heading, before Follow: a step or container picker. */
  actions?: React.ReactNode;
  /** The pane's height. Defaults to `h-96`. */
  paneClassName?: string;
  className?: string;
}

const CHUNK = 200;

const LEVEL: Record<LogLevel, { label: string; className: string }> = {
  error: { label: "ERR", className: "text-danger-fg" },
  warn: { label: "WRN", className: "text-warning-fg" },
  info: { label: "INF", className: "text-info-fg" },
  debug: { label: "DBG", className: "text-muted-foreground" },
  other: { label: "", className: "" },
};

export function LogView({
  lines,
  title,
  onDownload,
  loading = false,
  error,
  onRetry,
  emptyHint,
  actions,
  paneClassName = "h-96",
  className,
}: LogViewProps) {
  const t = useTranslations("runLog");
  const pane = React.useRef<HTMLDivElement | null>(null);
  const follow = useFollow(lines.length);
  const { following, pin } = follow;
  React.useLayoutEffect(() => {
    if (following && pane.current) pin(pane.current);
  }, [following, pin, lines.length]);

  const chunks = React.useMemo(() => {
    const out: LogLine[][] = [];
    for (let i = 0; i < lines.length; i += CHUNK) out.push(lines.slice(i, i + CHUNK));
    return out;
  }, [lines]);

  return (
    <Panel
      title={title ?? t("title")}
      icon={<ScrollTextIcon className="size-4" />}
      loading={loading && lines.length === 0}
      error={lines.length === 0 ? error : null}
      onRetry={onRetry}
      flush
      className={className}
      actions={
        <>
          {actions}
          <Button
            type="button"
            size="sm"
            variant={follow.following ? "secondary" : "outline"}
            aria-pressed={follow.following}
            onClick={() => follow.setFollowing(!follow.following)}
          >
            {t("follow")}
          </Button>
          {onDownload && (
            <Button
              type="button"
              size="icon"
              variant="outline"
              className="size-8"
              onClick={onDownload}
              disabled={lines.length === 0}
              aria-label={t("download")}
              title={t("download")}
            >
              <DownloadIcon className="size-4" />
            </Button>
          )}
        </>
      }
    >
      <div className="relative min-w-0">
        <div
          ref={pane}
          onScroll={follow.onScroll}
          role="log"
          aria-label={title ?? t("title")}
          aria-live={follow.following ? "polite" : "off"}
          tabIndex={0}
          className={cn(
            "bg-muted/30 focus-visible:ring-ring min-w-0 overflow-y-auto py-2 font-mono text-xs leading-relaxed focus-visible:ring-2 focus-visible:outline-none",
            paneClassName
          )}
        >
          {lines.length === 0 ? (
            <p className="text-muted-foreground px-4 italic">
              {emptyHint === undefined ? t("empty") : emptyHint}
            </p>
          ) : (
            chunks.map((chunk, i) => <Chunk key={i} lines={chunk} />)
          )}
        </div>
        {!follow.following && lines.length > 0 && (
          <Button
            type="button"
            size="sm"
            className="absolute right-4 bottom-3 shadow-md"
            onClick={() => follow.setFollowing(true)}
          >
            <ArrowDownToLineIcon className="size-3.5" />
            {t("jump")}
            {follow.unseen > 0 && (
              <span className="font-mono tabular-nums">
                · {t("newLines", { count: follow.unseen })}
              </span>
            )}
          </Button>
        )}
      </div>
    </Panel>
  );
}

/**
 * A fixed run of lines. Memoised on its first and last line and length, so
 * an append re-renders only the tail chunk; off-screen chunks skip layout.
 */
const Chunk = React.memo(
  function Chunk({ lines }: { lines: LogLine[] }) {
    return (
      <div className="[contain-intrinsic-size:auto_15rem] [content-visibility:auto]">
        {lines.map((line, i) => (
          <Row key={i} line={line} />
        ))}
      </div>
    );
  },
  (a, b) =>
    a.lines.length === b.lines.length &&
    a.lines[0] === b.lines[0] &&
    a.lines[a.lines.length - 1] === b.lines[b.lines.length - 1]
);

function Row({ line }: { line: LogLine }) {
  const supplied = line.level ?? classifyLogLevel(line.message);
  const key = typeof supplied === "string" ? supplied.toLowerCase() : "other";
  const known = Object.hasOwn(LEVEL, key);
  const level = known ? (key as LogLevel) : "other";
  const label = known ? LEVEL[level].label : typeof supplied === "string" ? supplied : "";
  const time = formatLogTime(line.ts);
  return (
    <div data-level={level} className="hover:bg-muted/50 flex min-w-0 gap-3 px-4">
      <time dateTime={time.full} title={time.full} className="text-muted-foreground shrink-0">
        {time.short}
      </time>
      <span
        className={cn("w-7 shrink-0 truncate", LEVEL[level].className)}
        title={typeof supplied === "string" ? supplied : undefined}
      >
        {label}
      </span>
      <span className="min-w-0 flex-1 [overflow-wrap:anywhere] whitespace-pre-wrap">
        {line.message}
      </span>
    </div>
  );
}
