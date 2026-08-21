"use client";

/**
 * LogViewer — shared log-tail surface used by both the per-app
 * Observability page and the Console page. Wraps the raw log buffer
 * with operator affordances (#421):
 *
 *   A. in-buffer search + match nav (regex toggle, prev/next, scroll-to)
 *   B. log-level filter (heuristic ERROR / WARN / INFO / DEBUG)
 *   C. multi-container picker (sourced from pod.containerStatuses)
 *   D. copy + download (clipboard + blob: URL, sanitized filename)
 *
 * The component is presentational: subscription lifecycle (open / close
 * / pause) lives in the parent. We accept a `lines` array of structured
 * log entries plus a pod context (slug, env, pod name, container list)
 * and emit `onContainerChange` upward so the parent can re-open the
 * subscription against the new container.
 */

import {
  ArrowDownIcon,
  ArrowUpIcon,
  BoxesIcon,
  ClipboardCopyIcon,
  DownloadIcon,
  RegexIcon,
  SearchIcon,
  XIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftAppLogLine } from "@/graphql/lifecycle/lifecycle.types";
import { cn } from "@/lib/utils";

/** Heuristic log levels parsed from the raw line text. */
export type LogLevel = "error" | "warn" | "info" | "debug" | "other";

const LEVEL_ORDER: readonly ("all" | LogLevel)[] = [
  "all",
  "error",
  "warn",
  "info",
  "debug",
  "other",
] as const;

export type LogLevelFilter = (typeof LEVEL_ORDER)[number];

// Match the keyword anywhere in the line, case-insensitive. We bias
// for the common "[ERROR]" / "level=error" / "ERROR " patterns by
// looking for the token at a word boundary — this avoids classifying
// the substring "info" inside the word "information" as INFO.
const LEVEL_PATTERNS: Record<LogLevel, RegExp | null> = {
  error: /\b(error|err|fatal|crit|critical|exception|panic)\b/i,
  warn: /\b(warn|warning)\b/i,
  info: /\b(info|notice)\b/i,
  debug: /\b(debug|trace)\b/i,
  other: null,
};

/** Classify a single log message into a heuristic level. */
export function classifyLogLevel(message: string): LogLevel {
  // Order matters: a line that says "ERROR: bad info" should classify
  // as error, not info.
  if (LEVEL_PATTERNS.error!.test(message)) return "error";
  if (LEVEL_PATTERNS.warn!.test(message)) return "warn";
  if (LEVEL_PATTERNS.info!.test(message)) return "info";
  if (LEVEL_PATTERNS.debug!.test(message)) return "debug";
  return "other";
}

const LEVEL_BADGE_CLASS: Record<LogLevel, string> = {
  error: "text-danger-fg bg-danger/10 border-danger-border",
  warn: "text-warning-fg bg-warning/10 border-warning-border",
  info: "text-info-fg bg-info/10 border-info-border",
  debug: "text-muted-foreground bg-muted/50 border-border",
  other: "text-muted-foreground bg-muted/30 border-border",
};

export interface LogViewerProps {
  /** Structured log buffer. The newest line is the last element. */
  lines: AstroliftAppLogLine[];
  /** Slug used in the download filename (`<slug>-<env>-<pod>-<ts>.log`). */
  appSlug: string;
  /** Environment name used in the download filename. Falls back to "all". */
  environmentName?: string | null;
  /** Pod name used in the download filename. */
  podName?: string | null;
  /** Containers available on the currently-selected pod. */
  containers?: string[];
  /** Currently-selected container (null/empty = all containers). */
  selectedContainer?: string | null;
  /** Callback when the operator changes the container filter. */
  onContainerChange?: (container: string | null) => void;
  /** Loading state for the underlying source (covers initial fetch). */
  loading?: boolean;
  /** Placeholder shown when the buffer is empty. */
  emptyHint?: React.ReactNode;
  /** Soft cap (informational — display only; parent enforces buffering). */
  bufferLimit?: number;
  /** Pixel height for the log pane. */
  className?: string;
  /**
   * #482 — render a per-line pod badge in front of the message. Used
   * by the "All replicas" aggregated view so the operator can tell
   * which replica logged each line at a glance. Default false keeps
   * the existing single-pod tail's tight line format.
   */
  showPodBadge?: boolean;
}

const LOG_PANE_BASE =
  "bg-muted/40 h-72 overflow-auto rounded-md border p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap [user-select:text] selection:bg-primary/30";

/** Format one structured line into the rendered string. */
function formatLine(line: AstroliftAppLogLine): string {
  const ts = (() => {
    try {
      return new Date(line.timestamp).toISOString();
    } catch {
      return String(line.timestamp);
    }
  })();
  const pod = line.podName.length > 30 ? line.podName.slice(-30) : line.podName;
  const container = line.container ? ` (${line.container})` : "";
  return `[${ts}] ${pod}${container} ${line.message}`;
}

/** Escape a string so it can be safely used inside a RegExp literal. */
function escapeRegExp(input: string): string {
  return input.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Build a RegExp from the search query, honoring the regex toggle. */
function buildSearchRegex(query: string, isRegex: boolean): RegExp | null {
  if (!query) return null;
  try {
    return new RegExp(isRegex ? query : escapeRegExp(query), "gi");
  } catch {
    return null;
  }
}

/**
 * Pre-segment a rendered line into [plain, match, plain, match, …]
 * runs so we can render each match as a <mark> with a stable
 * `data-match-index` attribute (used for scroll-into-view and "of M"
 * highlighting). Returns the segments plus the absolute match indexes
 * each <mark> should carry.
 */
function segmentLine(
  text: string,
  regex: RegExp | null,
  globalOffset: number
): { segments: Array<{ text: string; matchIndex: number | null }>; matchCount: number } {
  if (!regex) {
    return { segments: [{ text, matchIndex: null }], matchCount: 0 };
  }
  const segments: Array<{ text: string; matchIndex: number | null }> = [];
  // Cloning ensures we don't mutate the caller's `lastIndex` between
  // lines (we walk many lines in sequence with the same regex).
  const re = new RegExp(regex.source, regex.flags);
  let last = 0;
  let m: RegExpExecArray | null;
  let n = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) {
      segments.push({ text: text.slice(last, m.index), matchIndex: null });
    }
    segments.push({
      text: m[0] ?? "",
      matchIndex: globalOffset + n,
    });
    last = m.index + (m[0]?.length ?? 0);
    n += 1;
    // Defensive: zero-width regexes (e.g. `^`, `(?=)`) would loop
    // forever; advance one char to break out.
    if (m[0] === "") re.lastIndex += 1;
  }
  if (last < text.length) {
    segments.push({ text: text.slice(last), matchIndex: null });
  }
  return { segments, matchCount: n };
}

// #482 — stable hash → palette index so the same pod always gets the
// same badge color across renders without us tracking assignments in
// state. djb2 hash + modulo. The palette is intentionally small (six
// hues) so adjacent replicas stay visually distinct on small fleets.
const POD_BADGE_PALETTE = [
  "border-info-border bg-info/10 text-info-fg",
  "border-success-border bg-success/10 text-success-fg",
  "border-lime-600/40 bg-lime-500/10 text-lime-700 dark:text-lime-300",
  "border-warning-border bg-warning/10 text-warning-fg",
  "border-danger-border bg-danger/10 text-danger-fg",
  "border-border bg-muted text-muted-foreground",
] as const;

function podBadgeClass(podName: string): string {
  let hash = 5381;
  for (let i = 0; i < podName.length; i += 1) {
    hash = ((hash << 5) + hash + podName.charCodeAt(i)) | 0;
  }
  return POD_BADGE_PALETTE[Math.abs(hash) % POD_BADGE_PALETTE.length];
}

export function LogViewer({
  lines,
  appSlug,
  environmentName,
  podName,
  containers,
  selectedContainer,
  onContainerChange,
  loading,
  emptyHint,
  bufferLimit,
  className,
  showPodBadge,
}: LogViewerProps) {
  const t = useTranslations("apps.logViewer");

  const [query, setQuery] = React.useState("");
  const [isRegex, setIsRegex] = React.useState(false);
  const [levelFilter, setLevelFilter] = React.useState<LogLevelFilter>("all");
  const [currentMatch, setCurrentMatch] = React.useState(0);

  const searchInputRef = React.useRef<HTMLInputElement | null>(null);
  const paneRef = React.useRef<HTMLPreElement | null>(null);

  // Pre-classify every line once. Even at the 500-line cap this is
  // ~500 regex tests, well below a perceivable frame budget.
  const classified = React.useMemo(
    () =>
      lines.map((line) => ({
        line,
        level: classifyLogLevel(line.message),
        rendered: formatLine(line),
      })),
    [lines]
  );

  // Live per-level counts feed the filter chips ("Error · 4").
  const levelCounts = React.useMemo(() => {
    const counts: Record<LogLevel, number> = {
      error: 0,
      warn: 0,
      info: 0,
      debug: 0,
      other: 0,
    };
    for (const row of classified) counts[row.level] += 1;
    return counts;
  }, [classified]);

  // Apply level filter first (cheaper than running the regex on rows
  // we'll discard).
  const levelFiltered = React.useMemo(() => {
    if (levelFilter === "all") return classified;
    return classified.filter((r) => r.level === levelFilter);
  }, [classified, levelFilter]);

  const searchRegex = React.useMemo(() => buildSearchRegex(query, isRegex), [query, isRegex]);

  const regexError = query.length > 0 && isRegex && searchRegex === null;

  // Build the segmented render once per (lines, filter, query) tuple.
  // We walk each line, count its matches, and accumulate the global
  // match index so each <mark> gets a stable index for scrolling. Use
  // reduce so the running offset is part of the accumulator rather
  // than a mutated outer variable.
  const { segmentedLines, totalMatches } = React.useMemo(() => {
    const seed = {
      offset: 0,
      lines: [] as Array<{
        row: (typeof levelFiltered)[number];
        segments: ReturnType<typeof segmentLine>["segments"];
      }>,
    };
    const folded = levelFiltered.reduce((acc, row) => {
      const { segments, matchCount } = segmentLine(row.rendered, searchRegex, acc.offset);
      return {
        offset: acc.offset + matchCount,
        lines: [...acc.lines, { row, segments }],
      };
    }, seed);
    return { segmentedLines: folded.lines, totalMatches: folded.offset };
  }, [levelFiltered, searchRegex]);

  // Reset the active match when the search shape changes so we don't
  // point at an index that no longer exists. Use the during-render
  // state-reset pattern (cheaper + lint-clean) instead of useEffect.
  const searchShape = `${query}::${isRegex ? "r" : "p"}::${levelFilter}`;
  const [prevSearchShape, setPrevSearchShape] = React.useState(searchShape);
  if (prevSearchShape !== searchShape) {
    setPrevSearchShape(searchShape);
    if (currentMatch !== 0) setCurrentMatch(0);
  }

  // Scroll the active match into view whenever it changes. We look up
  // the <mark> by data attribute so the lookup survives re-renders.
  React.useEffect(() => {
    if (totalMatches === 0) return;
    const pane = paneRef.current;
    if (!pane) return;
    const target = pane.querySelector<HTMLElement>(`mark[data-match-index="${currentMatch}"]`);
    if (target) {
      target.scrollIntoView({ block: "center", behavior: "auto" });
    }
  }, [currentMatch, totalMatches, segmentedLines]);

  // Auto-tail: snap to bottom when new lines arrive *iff* the operator
  // is already near the bottom and no search is active (active search
  // wants stable framing on the current match).
  React.useEffect(() => {
    if (searchRegex) return;
    const el = paneRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [segmentedLines, searchRegex]);

  const goPrev = React.useCallback(() => {
    if (totalMatches === 0) return;
    setCurrentMatch((i) => (i - 1 + totalMatches) % totalMatches);
  }, [totalMatches]);

  const goNext = React.useCallback(() => {
    if (totalMatches === 0) return;
    setCurrentMatch((i) => (i + 1) % totalMatches);
  }, [totalMatches]);

  // Keyboard shortcuts — `/` focuses search, Enter/Shift-Enter navigates
  // matches, Esc clears the query. Bound at component scope so they're
  // active whenever the viewer is on screen; we ignore `/` when the
  // operator is already typing into an input/textarea elsewhere on the
  // page.
  React.useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const inEditable = (() => {
        const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
        const editable = (e.target as HTMLElement | null)?.isContentEditable;
        return tag === "input" || tag === "textarea" || tag === "select" || !!editable;
      })();

      // The search input itself is focusable: while it's focused we
      // want Enter / Shift-Enter / Esc to operate on the viewer, but
      // we don't want a `/` keypress to be intercepted (the operator
      // is typing the character).
      const inOurSearch = e.target === searchInputRef.current;

      if (e.key === "/" && !inEditable && !inOurSearch) {
        e.preventDefault();
        searchInputRef.current?.focus();
        return;
      }
      if (inOurSearch && e.key === "Enter") {
        e.preventDefault();
        if (e.shiftKey) goPrev();
        else goNext();
        return;
      }
      if (inOurSearch && e.key === "Escape") {
        e.preventDefault();
        setQuery("");
        searchInputRef.current?.blur();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goNext, goPrev]);

  // Hide the container picker when there's nothing to pick — single
  // container or no pod selected. Avoids visual noise on simple apps.
  const showContainerPicker = !!containers && containers.length > 1;

  const safeContainerValue = selectedContainer ?? "__all__";

  const renderedJoinedForCopy = React.useMemo(
    () => levelFiltered.map((r) => r.rendered).join("\n"),
    [levelFiltered]
  );

  const onCopyAll = React.useCallback(async () => {
    if (renderedJoinedForCopy.length === 0) {
      toast.info(t("copyEmpty"));
      return;
    }
    try {
      await navigator.clipboard.writeText(renderedJoinedForCopy);
      toast.success(
        t("copied", {
          count: levelFiltered.length,
        })
      );
    } catch {
      toast.error(t("copyFailed"));
    }
  }, [renderedJoinedForCopy, levelFiltered.length, t]);

  const onDownload = React.useCallback(() => {
    if (renderedJoinedForCopy.length === 0) {
      toast.info(t("downloadEmpty"));
      return;
    }
    const iso = new Date().toISOString().replace(/[:.]/g, "-");
    // Sanitize each filename segment so we don't end up with empty
    // tokens or path separators.
    const slugSafe = (input: string | null | undefined, fallback: string) =>
      (input ?? "").replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || fallback;
    const filename =
      [
        slugSafe(appSlug, "app"),
        slugSafe(environmentName, "all"),
        slugSafe(podName, "pod"),
        iso,
      ].join("-") + ".log";

    const blob = new Blob([renderedJoinedForCopy + "\n"], {
      type: "text/plain;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    // Defer revoke so the browser definitely captured the navigation.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast.success(t("downloadStarted", { filename }));
  }, [renderedJoinedForCopy, appSlug, environmentName, podName, t]);

  return (
    <div className="space-y-3">
      {/* ─── controls row ────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1 sm:flex-initial sm:basis-72">
          <SearchIcon
            aria-hidden="true"
            className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2"
          />
          <Input
            ref={searchInputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("searchPlaceholder")}
            aria-label={t("searchAriaLabel")}
            aria-invalid={regexError || undefined}
            className={cn(
              "pr-16 pl-7 font-mono text-xs",
              regexError && "border-destructive focus-visible:ring-destructive/40"
            )}
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery("")}
              aria-label={t("clearSearch")}
              className="text-muted-foreground hover:text-foreground absolute top-1/2 right-9 -translate-y-1/2"
            >
              <XIcon className="size-3.5" />
            </button>
          )}
          <button
            type="button"
            onClick={() => setIsRegex((v) => !v)}
            aria-pressed={isRegex}
            aria-label={t("regexToggle")}
            title={t("regexToggle")}
            className={cn(
              "absolute top-1/2 right-2 -translate-y-1/2 rounded p-0.5 transition-colors",
              isRegex ? "bg-primary/15 text-primary" : "text-muted-foreground hover:text-foreground"
            )}
          >
            <RegexIcon className="size-3.5" />
          </button>
        </div>

        <div className="flex items-center gap-1">
          <span
            aria-live="polite"
            className="text-muted-foreground min-w-[5.5rem] text-center font-mono text-xs"
          >
            {regexError
              ? t("regexInvalid")
              : query
                ? totalMatches === 0
                  ? t("matchNone")
                  : t("matchCount", {
                      current: currentMatch + 1,
                      total: totalMatches,
                    })
                : ""}
          </span>
          <Button
            type="button"
            size="icon"
            variant="outline"
            className="size-7"
            onClick={goPrev}
            disabled={totalMatches === 0}
            aria-label={t("prevMatch")}
            title={t("prevMatch")}
          >
            <ArrowUpIcon className="size-3.5" />
          </Button>
          <Button
            type="button"
            size="icon"
            variant="outline"
            className="size-7"
            onClick={goNext}
            disabled={totalMatches === 0}
            aria-label={t("nextMatch")}
            title={t("nextMatch")}
          >
            <ArrowDownIcon className="size-3.5" />
          </Button>
        </div>

        {showContainerPicker && (
          <div className="flex items-center gap-1.5">
            <BoxesIcon aria-hidden="true" className="text-muted-foreground size-3.5" />
            <Select
              value={safeContainerValue}
              onValueChange={(v) => onContainerChange?.(v === "__all__" ? null : v)}
            >
              <SelectTrigger
                size="sm"
                aria-label={t("containerLabel")}
                className="font-mono text-xs"
              >
                <SelectValue placeholder={t("containerAll")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">{t("containerAll")}</SelectItem>
                {containers!.map((c) => (
                  <SelectItem key={c} value={c} className="font-mono">
                    {c}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        <div className="ml-auto flex items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={onCopyAll}
            disabled={levelFiltered.length === 0}
          >
            <ClipboardCopyIcon className="size-3" /> {t("copyAll")}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={onDownload}
            disabled={levelFiltered.length === 0}
          >
            <DownloadIcon className="size-3" /> {t("download")}
          </Button>
        </div>
      </div>

      {/* ─── level filter chips ──────────────────────────────────────── */}
      <div
        className="flex flex-wrap items-center gap-1.5"
        role="group"
        aria-label={t("levelGroupLabel")}
      >
        {LEVEL_ORDER.map((level) => {
          const count = level === "all" ? classified.length : levelCounts[level as LogLevel];
          const active = level === levelFilter;
          return (
            <button
              key={level}
              type="button"
              onClick={() => setLevelFilter(level)}
              aria-pressed={active}
              className={cn(
                "rounded-full border px-2 py-0.5 font-mono text-2xs transition-colors",
                active
                  ? "border-primary bg-primary/10 text-primary"
                  : level === "all"
                    ? "border-border text-muted-foreground hover:text-foreground"
                    : cn(LEVEL_BADGE_CLASS[level as LogLevel], "opacity-70 hover:opacity-100")
              )}
            >
              {t(`level.${level}`)} · {count}
            </button>
          );
        })}
      </div>

      {/* ─── log pane ────────────────────────────────────────────────── */}
      <pre ref={paneRef} className={cn(LOG_PANE_BASE, className)} aria-label={t("paneAriaLabel")}>
        {loading && lines.length === 0 ? (
          <div className="space-y-2">
            <Skeleton className="h-3 w-1/2" />
            <Skeleton className="h-3 w-2/3" />
            <Skeleton className="h-3 w-3/4" />
          </div>
        ) : segmentedLines.length === 0 ? (
          <span className="text-muted-foreground italic">{emptyHint}</span>
        ) : (
          segmentedLines.map(({ row, segments }, idx) => (
            // One log entry per <div>; the parent <pre> preserves
            // whitespace so a literal "\n" text node renders as a
            // newline in user selection / copy. Block-level <div>
            // already breaks lines visually.
            <div key={idx} data-level={row.level}>
              {showPodBadge && row.line.podName ? (
                <span
                  className={cn(
                    "mr-1.5 inline-block rounded border px-1 py-px align-middle text-2xs leading-none",
                    podBadgeClass(row.line.podName)
                  )}
                  title={row.line.podName}
                >
                  {/* Short the pod name to its last 14 chars — replica
                      hashes live at the tail and that's the disambiguating
                      part. Full name lives in the title attr. */}
                  {row.line.podName.length > 14
                    ? `…${row.line.podName.slice(-14)}`
                    : row.line.podName}
                </span>
              ) : null}
              {segments.map((seg, sidx) =>
                seg.matchIndex === null ? (
                  <span key={sidx}>{seg.text}</span>
                ) : (
                  <mark
                    key={sidx}
                    data-match-index={seg.matchIndex}
                    className={cn(
                      "rounded-sm px-0.5",
                      seg.matchIndex === currentMatch
                        ? "bg-amber-400/70 text-amber-950 ring-1 ring-amber-500 dark:bg-amber-500/80 dark:text-amber-50"
                        : "bg-yellow-300/40 text-inherit dark:bg-yellow-300/30"
                    )}
                  >
                    {seg.text}
                  </mark>
                )
              )}
            </div>
          ))
        )}
      </pre>

      {bufferLimit !== undefined && (
        <p className="text-muted-foreground text-xs">
          {t("bufferFootnote", {
            shown: levelFiltered.length,
            buffered: lines.length,
            limit: bufferLimit,
          })}
        </p>
      )}
    </div>
  );
}
