"use client";

import {
  AlertTriangleIcon,
  AppWindowIcon,
  BuildingIcon,
  ChevronRightIcon,
  FolderIcon,
  Loader2Icon,
  SearchIcon,
  UsersIcon,
} from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import {
  SCOPE_NOUN,
  type ScopeKind,
  type ScopeNode,
  type ScopeRef,
  scopePath,
} from "./access-model";

const ICON: Record<ScopeKind, typeof BuildingIcon> = {
  ORG: BuildingIcon,
  TEAM: UsersIcon,
  PROJECT: FolderIcon,
  APP: AppWindowIcon,
};

export interface ScopePickerProps {
  /** Usually one node: the organization, with its teams below. */
  roots: ScopeNode[];
  value: ScopeRef | null;
  onChange: (scope: ScopeNode) => void;
  /** Children of a node that has them but did not come with them, fetched on expand. */
  loadChildren?: (node: ScopeNode) => Promise<ScopeNode[]>;
  /** `true`, or the reason the node cannot be picked (the role binds at another level). */
  selectable?: (node: ScopeNode) => true | string;
  loading?: boolean;
  error?: { message: string } | null;
  onRetry?: () => void;
  /** Names the tree for assistive tech. */
  label?: string;
  className?: string;
}

const keyOf = (n: { kind: ScopeKind; id: string }) => `${n.kind}:${n.id}`;

interface Row {
  node: ScopeNode;
  level: number;
  expandable: boolean;
  expanded: boolean;
}

/**
 * Where a grant applies (design 3.4, step 3): org › teams › projects › apps
 * as a tree. The current entity is preselected and its path open; children
 * a node did not come with load when it is expanded, once. Search narrows
 * to matches and their ancestors among what is loaded. Nodes the chosen role
 * cannot bind at stay visible, disabled, with the reason. Scrolls in its own
 * frame; arrow keys move, Right and Left open and close, Enter picks.
 */
export function ScopePicker({
  roots,
  value,
  onChange,
  loadChildren,
  selectable = () => true,
  loading = false,
  error,
  onRetry,
  label = "Scope",
  className,
}: ScopePickerProps) {
  const [query, setQuery] = React.useState("");
  const [loaded, setLoaded] = React.useState<Record<string, ScopeNode[]>>({});
  const [pending, setPending] = React.useState<Record<string, boolean>>({});
  const [failed, setFailed] = React.useState<Record<string, string>>({});
  // Open by default: the roots and the path to the selection, derived so a
  // tree that arrives after the first render still opens on its selection.
  // A person's own toggles override the default.
  const [toggled, setToggled] = React.useState<Record<string, boolean>>({});
  const defaultOpen = React.useMemo(() => {
    const open = new Set<string>(roots.map(keyOf));
    if (value) for (const n of scopePath(roots, value.kind, value.id) ?? []) open.add(keyOf(n));
    return open;
  }, [roots, value]);
  const isOpen = React.useCallback(
    (key: string) => toggled[key] ?? defaultOpen.has(key),
    [toggled, defaultOpen]
  );
  const [focusKey, setFocusKey] = React.useState<string | null>(value ? keyOf(value) : null);
  const treeRef = React.useRef<HTMLUListElement>(null);

  const childrenOf = React.useCallback(
    (n: ScopeNode): ScopeNode[] | undefined => n.children ?? loaded[keyOf(n)],
    [loaded]
  );

  const needle = query.trim().toLowerCase();
  const matches = React.useCallback(
    (n: ScopeNode) =>
      n.name.toLowerCase().includes(needle) || (n.slug ?? "").toLowerCase().includes(needle),
    [needle]
  );

  // Flatten what is visible: while searching, a node shows when it or a
  // descendant matches, and every ancestor of a match is open.
  const rows = React.useMemo(() => {
    const out: Row[] = [];
    const visit = (nodes: ScopeNode[], level: number): boolean => {
      let any = false;
      for (const node of nodes) {
        const kids = childrenOf(node);
        const expandable = Boolean(kids?.length) || (Boolean(node.hasChildren) && !kids);
        const start = out.length;
        const row: Row = {
          node,
          level,
          expandable,
          expanded: needle ? true : isOpen(keyOf(node)),
        };
        out.push(row);
        let below = false;
        if (kids && row.expanded) below = visit(kids, level + 1);
        const keep = !needle || matches(node) || below;
        if (!keep) out.splice(start);
        any = any || keep;
      }
      return any;
    };
    visit(roots, 1);
    return out;
  }, [roots, childrenOf, isOpen, needle, matches]);

  async function toggle(node: ScopeNode, open?: boolean) {
    const key = keyOf(node);
    const willOpen = open ?? !isOpen(key);
    setToggled((prev) => ({ ...prev, [key]: willOpen }));
    if (willOpen && !childrenOf(node) && node.hasChildren && loadChildren && !pending[key]) {
      setPending((p) => ({ ...p, [key]: true }));
      setFailed(({ [key]: _gone, ...rest }) => rest);
      try {
        const kids = await loadChildren(node);
        setLoaded((l) => ({ ...l, [key]: kids }));
      } catch (err) {
        setFailed((f) => ({ ...f, [key]: err instanceof Error ? err.message : "Could not load" }));
      } finally {
        setPending(({ [key]: _done, ...rest }) => rest);
      }
    }
  }

  function pick(node: ScopeNode) {
    if (selectable(node) === true) onChange(node);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    const i = rows.findIndex((r) => keyOf(r.node) === focusKey);
    const row = rows[i];
    const move = (to: number) => {
      const target = rows[Math.max(0, Math.min(rows.length - 1, to))];
      if (!target) return;
      setFocusKey(keyOf(target.node));
      treeRef.current
        ?.querySelector<HTMLElement>(`[data-key="${CSS.escape(keyOf(target.node))}"]`)
        ?.focus();
    };
    switch (e.key) {
      case "ArrowDown":
        move(i + 1);
        break;
      case "ArrowUp":
        move(i - 1);
        break;
      case "Home":
        move(0);
        break;
      case "End":
        move(rows.length - 1);
        break;
      case "ArrowRight":
        if (row?.expandable && !row.expanded) void toggle(row.node, true);
        else move(i + 1);
        break;
      case "ArrowLeft":
        if (row?.expandable && row.expanded && !needle) void toggle(row.node, false);
        else {
          for (let j = i - 1; j >= 0; j--) {
            if (rows[j].level < (row?.level ?? 0)) {
              move(j);
              break;
            }
          }
        }
        break;
      case "Enter":
      case " ":
        if (row) pick(row.node);
        break;
      default:
        return;
    }
    e.preventDefault();
  }

  if (loading) {
    return (
      <div className={cn("flex min-w-0 flex-col gap-2", className)} aria-busy>
        <Skeleton className="h-9 w-full" />
        {Array.from({ length: 5 }, (_, i) => (
          <Skeleton key={i} className="h-7" style={{ marginLeft: `${(i % 3) * 1.25}rem` }} />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div
        role="alert"
        className="border-danger-border bg-danger-bg text-danger-fg flex min-w-0 flex-wrap items-center gap-2 rounded-md border p-3 text-sm"
      >
        <AlertTriangleIcon className="size-4 shrink-0" />
        <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
          Could not load scopes: {error.message}
        </span>
        {onRetry && (
          <Button size="sm" variant="outline" onClick={onRetry}>
            Retry
          </Button>
        )}
      </div>
    );
  }

  const focused = focusKey ? rows.find((r) => keyOf(r.node) === focusKey)?.node : undefined;
  const tabbable = focused ? focusKey : null;
  const focusedReason = focused ? selectable(focused) : true;

  return (
    <div className={cn("flex min-w-0 flex-col gap-2", className)}>
      <div className="relative min-w-0">
        <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
        <Input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search teams, projects, apps…"
          aria-label={`Search ${label.toLowerCase()}`}
          className="pl-8"
        />
      </div>
      {value && (
        <p className="text-muted-foreground min-w-0 text-xs [overflow-wrap:anywhere]">
          Selected: {SCOPE_NOUN[value.kind]} <span className="font-mono">{value.name}</span>
        </p>
      )}
      <div className="max-h-80 min-w-0 overflow-auto rounded-md border">
        {rows.length === 0 ? (
          <p className="text-muted-foreground p-3 text-sm">
            {needle ? `Nothing loaded matches "${query.trim()}".` : "No scopes to choose from."}
          </p>
        ) : (
          <ul role="tree" aria-label={label} ref={treeRef} onKeyDown={onKeyDown} className="py-1">
            {rows.map((row, idx) => {
              const { node } = row;
              const key = keyOf(node);
              const Icon = ICON[node.kind];
              const allowed = selectable(node);
              const selected = value ? keyOf(value) === key : false;
              const isPending = pending[key];
              return (
                <li
                  key={key}
                  role="treeitem"
                  data-key={key}
                  aria-level={row.level}
                  aria-expanded={row.expandable ? row.expanded : undefined}
                  aria-selected={selected}
                  aria-disabled={allowed !== true || undefined}
                  tabIndex={key === tabbable || (!tabbable && idx === 0) ? 0 : -1}
                  onFocus={() => setFocusKey(key)}
                  onClick={() => pick(node)}
                  className={cn(
                    "focus-visible:ring-ring flex min-w-0 cursor-pointer items-center gap-1.5 py-1 pr-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-inset",
                    selected ? "bg-primary/10 text-foreground" : "hover:bg-muted/60",
                    allowed !== true && "text-muted-foreground cursor-not-allowed"
                  )}
                  style={{ paddingLeft: `${(row.level - 1) * 1.25 + 0.5}rem` }}
                >
                  <span className="flex size-5 shrink-0 items-center justify-center">
                    {row.expandable &&
                      (isPending ? (
                        <Loader2Icon aria-label="Loading" className="size-3.5 animate-spin" />
                      ) : (
                        <button
                          type="button"
                          tabIndex={-1}
                          aria-label={`${row.expanded ? "Collapse" : "Expand"} ${node.name}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            void toggle(node);
                          }}
                          className="hover:bg-muted rounded-sm p-0.5"
                        >
                          <ChevronRightIcon
                            className={cn(
                              "size-3.5 transition-transform duration-[var(--motion-fast)]",
                              row.expanded && "rotate-90"
                            )}
                          />
                        </button>
                      ))}
                  </span>
                  <Icon aria-hidden className="text-muted-foreground size-3.5 shrink-0" />
                  <span className="min-w-0 truncate" title={node.name}>
                    {node.name}
                  </span>
                  {node.slug && node.slug !== node.name && (
                    <span className="text-muted-foreground text-2xs min-w-0 truncate font-mono">
                      {node.slug}
                    </span>
                  )}
                  <span className="text-muted-foreground text-2xs ml-auto shrink-0 font-mono uppercase">
                    {SCOPE_NOUN[node.kind]}
                  </span>
                  {allowed !== true && <span className="sr-only">, {allowed}</span>}
                  {failed[key] && (
                    <span className="text-danger-fg text-2xs shrink-0" role="status">
                      {failed[key]}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
      {focusedReason !== true && (
        <p className="text-muted-foreground text-xs" aria-live="polite">
          {focusedReason}
        </p>
      )}
    </div>
  );
}
