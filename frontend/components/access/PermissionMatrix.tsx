"use client";

import { CheckIcon, ChevronRightIcon, MinusIcon, PlusIcon } from "lucide-react";
import * as React from "react";

import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { ASTROLIFT_PERMISSIONS } from "@/lib/permissions/permissions.generated";
import { cn } from "@/lib/utils";

import {
  buildMatrix,
  diffPermissions,
  type MatrixRow,
  resourceLabel,
  splitSlug,
  VERB_COLUMNS,
} from "./access-model";

export interface PermissionMatrixProps {
  /** The slugs held (read-only) or chosen (editable). */
  permissions: readonly string[];
  /** The catalog to lay out; defaults to every declared permission. */
  catalog?: readonly string[];
  /** Checkboxes per cell, per row and per area. Without it the matrix only shows. */
  onChange?: (next: string[]) => void;
  /** Diff mode: marks what was added and removed against these, e.g. the role it came from. */
  base?: readonly string[];
  /** Names `base` in the diff summary: "vs Viewer". */
  baseLabel?: string;
  /** Resources with nothing held are hidden (read-only); editing always shows every row. */
  showEmpty?: boolean;
  /** Areas open on first render; defaults to every area with something held. */
  defaultOpen?: string[];
  className?: string;
}

type CellState = "on" | "off" | "added" | "removed";

/**
 * A role's permissions as areas × verbs (design 3.5): rows are resources
 * grouped by area (Agents, Apps, Workflows, Admin, then Other), columns are
 * Read, Create, Update, Delete, then the resource's own verbs (deploy,
 * rollback, exec_pod) as chips. Every cell is one catalog slug, in mono on
 * hover. Read-only by default; editable with `onChange` (a cell, a whole row,
 * a whole area); with `base`, a diff (added, removed) against another set.
 * Scrolls in its own frame with a sticky header; areas fold.
 */
export function PermissionMatrix({
  permissions,
  catalog = ASTROLIFT_PERMISSIONS,
  onChange,
  base,
  baseLabel,
  showEmpty,
  defaultOpen,
  className,
}: PermissionMatrixProps) {
  const editable = Boolean(onChange);
  const held = React.useMemo(() => new Set(permissions), [permissions]);
  const was = React.useMemo(() => (base ? new Set(base) : null), [base]);
  const [filter, setFilter] = React.useState("");
  const needle = filter.trim().toLowerCase();

  const areas = React.useMemo(() => buildMatrix(catalog), [catalog]);
  const [open, setOpen] = React.useState<Set<string>>(
    () =>
      new Set(
        defaultOpen ??
          areas
            .filter((a) => a.rows.some((r) => r.all.some((s) => held.has(s) || was?.has(s))))
            .map((a) => a.key)
      )
  );

  const diff = base ? diffPermissions([...held], base) : null;
  const visibleEmpty = showEmpty ?? editable;

  const stateOf = (slug: string): CellState => {
    const on = held.has(slug);
    if (was) {
      if (on && !was.has(slug)) return "added";
      if (!on && was.has(slug)) return "removed";
    }
    return on ? "on" : "off";
  };

  const setMany = (slugs: string[], on: boolean) => {
    const next = new Set(held);
    for (const s of slugs) {
      if (on) next.add(s);
      else next.delete(s);
    }
    onChange?.([...next].sort());
  };

  const rowVisible = (row: MatrixRow) => {
    if (needle && !row.all.some((s) => s.includes(needle)) && !row.resource.includes(needle)) {
      return false;
    }
    return visibleEmpty || row.all.some((s) => held.has(s) || was?.has(s));
  };

  const shownAreas = areas
    .map((a) => ({ ...a, rows: a.rows.filter(rowVisible) }))
    .filter((a) => a.rows.length > 0);

  const grid = "grid grid-cols-[minmax(7rem,1.2fr)_repeat(4,3.25rem)_minmax(7rem,2fr)]";

  return (
    <div className={cn("flex min-w-0 flex-col gap-2", className)}>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <Input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter permissions, e.g. deploy"
          aria-label="Filter permissions"
          className="h-8 max-w-64 min-w-0 flex-1 font-mono text-xs"
        />
        <span className="text-muted-foreground font-mono text-xs tabular-nums">
          {held.size} of {catalog.length}
        </span>
        {diff && (
          <span className="flex min-w-0 items-center gap-2 font-mono text-xs tabular-nums">
            <span className="text-success-fg">+{diff.added.length}</span>
            <span className="text-danger-fg">−{diff.removed.length}</span>
            {baseLabel && (
              <span className="text-muted-foreground min-w-0 truncate font-sans">
                vs {baseLabel}
              </span>
            )}
          </span>
        )}
      </div>

      <div className="max-h-[28rem] min-w-0 overflow-auto rounded-md border">
        {shownAreas.length === 0 ? (
          <p className="text-muted-foreground p-3 text-sm">
            {needle
              ? `No permission matches "${filter.trim()}".`
              : catalog.length === 0
                ? "The permission catalog has not loaded."
                : "No permissions."}
          </p>
        ) : (
          <div role="table" aria-label="Permissions" className="min-w-[30rem] text-sm">
            <div role="rowgroup" className="bg-surface-1 sticky top-0 z-10 border-b">
              <div role="row" className={cn(grid, "text-muted-foreground text-2xs uppercase")}>
                <span role="columnheader" className="px-3 py-2 font-medium tracking-wide">
                  Resource
                </span>
                {VERB_COLUMNS.map((c) => (
                  <span
                    key={c.key}
                    role="columnheader"
                    className="px-1 py-2 text-center font-medium tracking-wide"
                  >
                    {c.label}
                  </span>
                ))}
                <span role="columnheader" className="px-3 py-2 font-medium tracking-wide">
                  Also
                </span>
              </div>
            </div>
            {shownAreas.map((area) => {
              const isOpen = needle ? true : open.has(area.key);
              const areaSlugs = area.rows.flatMap((r) => r.all);
              const areaHeld = areaSlugs.filter((s) => held.has(s)).length;
              return (
                <div role="rowgroup" key={area.key} className="border-b last:border-b-0">
                  <div role="row" className="bg-muted/30 flex min-w-0 items-center gap-2 px-2 py-1">
                    <span role="rowheader" className="flex min-w-0 flex-1 items-center gap-1">
                      <button
                        type="button"
                        aria-expanded={isOpen}
                        onClick={() =>
                          setOpen((prev) => {
                            const next = new Set(prev);
                            if (next.has(area.key)) next.delete(area.key);
                            else next.add(area.key);
                            return next;
                          })
                        }
                        className="focus-visible:ring-ring hover:bg-muted flex min-w-0 items-center gap-1 rounded-sm px-1 py-0.5 text-xs font-medium focus-visible:ring-2 focus-visible:outline-none"
                      >
                        <ChevronRightIcon
                          aria-hidden
                          className={cn(
                            "size-3.5 transition-transform duration-[var(--motion-fast)]",
                            isOpen && "rotate-90"
                          )}
                        />
                        {area.label}
                      </button>
                      <span className="text-muted-foreground text-2xs font-mono tabular-nums">
                        {areaHeld}/{areaSlugs.length}
                      </span>
                    </span>
                    {editable && (
                      <Checkbox
                        aria-label={`All ${area.label} permissions`}
                        checked={
                          areaHeld === areaSlugs.length
                            ? true
                            : areaHeld > 0
                              ? "indeterminate"
                              : false
                        }
                        onCheckedChange={(v) => setMany(areaSlugs, v === true)}
                      />
                    )}
                  </div>
                  {isOpen &&
                    area.rows.map((row) => (
                      <MatrixRowView
                        key={row.resource}
                        row={row}
                        grid={grid}
                        editable={editable}
                        stateOf={stateOf}
                        onToggle={(slug, on) => setMany([slug], on)}
                        onRow={(on) => setMany(row.all, on)}
                      />
                    ))}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function MatrixRowView({
  row,
  grid,
  editable,
  stateOf,
  onToggle,
  onRow,
}: {
  row: MatrixRow;
  grid: string;
  editable: boolean;
  stateOf: (slug: string) => CellState;
  onToggle: (slug: string, on: boolean) => void;
  onRow: (on: boolean) => void;
}) {
  const heldCount = row.all.filter((s) => {
    const st = stateOf(s);
    return st === "on" || st === "added";
  }).length;
  return (
    <div role="row" className={cn(grid, "hover:bg-muted/30 items-center border-t")}>
      <span role="rowheader" className="flex min-w-0 items-center gap-2 px-3 py-1.5">
        {editable && (
          <Checkbox
            aria-label={`All ${resourceLabel(row.resource)} permissions`}
            checked={heldCount === row.all.length ? true : heldCount > 0 ? "indeterminate" : false}
            onCheckedChange={(v) => onRow(v === true)}
          />
        )}
        <span className="min-w-0 truncate font-mono text-xs" title={row.resource}>
          {row.resource}
        </span>
      </span>
      {VERB_COLUMNS.map((c) => {
        const slug = row.cells[c.key];
        return (
          <span role="cell" key={c.key} className="flex justify-center px-1 py-1.5">
            {slug ? (
              <Cell slug={slug} state={stateOf(slug)} editable={editable} onToggle={onToggle} />
            ) : (
              <span aria-label="not applicable" className="text-muted-foreground/50 text-xs">
                ·
              </span>
            )}
          </span>
        );
      })}
      <span role="cell" className="flex min-w-0 flex-wrap gap-1 px-3 py-1.5">
        {row.more.map((slug) => (
          <VerbChip
            key={slug}
            slug={slug}
            state={stateOf(slug)}
            editable={editable}
            onToggle={onToggle}
          />
        ))}
      </span>
    </div>
  );
}

const STATE_LABEL: Record<CellState, string> = {
  on: "granted",
  off: "not granted",
  added: "added",
  removed: "removed",
};

function Cell({
  slug,
  state,
  editable,
  onToggle,
}: {
  slug: string;
  state: CellState;
  editable: boolean;
  onToggle: (slug: string, on: boolean) => void;
}) {
  const on = state === "on" || state === "added";
  if (editable) {
    return (
      <span
        className={cn(
          "inline-flex rounded-sm p-0.5",
          state === "added" && "bg-success-bg ring-success-border ring-1",
          state === "removed" && "bg-danger-bg ring-danger-border ring-1"
        )}
        title={`${slug} (${STATE_LABEL[state]})`}
      >
        <Checkbox
          aria-label={slug}
          checked={on}
          onCheckedChange={(v) => onToggle(slug, v === true)}
        />
      </span>
    );
  }
  return (
    <span
      title={`${slug} (${STATE_LABEL[state]})`}
      aria-label={`${slug}: ${STATE_LABEL[state]}`}
      role="img"
      className={cn(
        "inline-flex size-5 items-center justify-center rounded-sm",
        state === "on" && "text-foreground",
        state === "off" && "text-muted-foreground/40",
        state === "added" && "bg-success-bg text-success-fg",
        state === "removed" && "bg-danger-bg text-danger-fg"
      )}
    >
      {state === "added" ? (
        <PlusIcon className="size-3.5" />
      ) : state === "removed" ? (
        <MinusIcon className="size-3.5" />
      ) : state === "on" ? (
        <CheckIcon className="size-3.5" />
      ) : (
        <span className="text-xs">·</span>
      )}
    </span>
  );
}

function VerbChip({
  slug,
  state,
  editable,
  onToggle,
}: {
  slug: string;
  state: CellState;
  editable: boolean;
  onToggle: (slug: string, on: boolean) => void;
}) {
  const on = state === "on" || state === "added";
  const verb = splitSlug(slug).verb;
  const cls = cn(
    "inline-flex max-w-full min-w-0 items-center gap-1 rounded-full border px-1.5 py-0.5 font-mono text-2xs",
    state === "on" && "border-border bg-muted text-foreground",
    state === "off" && "text-muted-foreground border-dashed",
    state === "added" && "border-success-border bg-success-bg text-success-fg",
    state === "removed" && "border-danger-border bg-danger-bg text-danger-fg line-through"
  );
  const inner = (
    <>
      {state === "added" && <PlusIcon aria-hidden className="size-3 shrink-0" />}
      {state === "removed" && <MinusIcon aria-hidden className="size-3 shrink-0" />}
      <span className="min-w-0 truncate">{verb}</span>
    </>
  );
  if (!editable) {
    if (state === "off") return null;
    return (
      <span className={cls} title={`${slug} (${STATE_LABEL[state]})`}>
        {inner}
        <span className="sr-only">: {STATE_LABEL[state]}</span>
      </span>
    );
  }
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={on}
      aria-label={slug}
      title={slug}
      onClick={() => onToggle(slug, !on)}
      className={cn(cls, "focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none")}
    >
      {inner}
    </button>
  );
}
