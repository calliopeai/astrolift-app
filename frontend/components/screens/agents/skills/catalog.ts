/**
 * What the Agents area's catalog lists share (spec 44 §4.4, §5.1): Skills,
 * Tools, Models, Functions and Workloads. The breadcrumb that starts at the
 * area's switcher, the list state spelled as a numbered page's variables
 * (the server answers every filter, sort and page, #2155), and the generic
 * filter, sort and slice step that story fixtures use as the server's
 * stand-in. Pure.
 */
import type { SortState } from "@/components/data-table";
import { formatSort, type ListView } from "@/components/list/list-state";
import type { Crumb } from "@/components/shell/ShellHeader";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";

export type AgentsFunction =
  | "skills"
  | "tools"
  | "models"
  | "functions"
  | "workloads"
  | "environment-specs";

const LABEL: Record<AgentsFunction, string> = {
  "environment-specs": "Environment specs",
  skills: "Skills",
  tools: "Tools",
  models: "Models",
  functions: "Functions",
  workloads: "Workloads",
};

const HREF: Record<AgentsFunction, string> = {
  "environment-specs": "/agents/environment-specs",
  skills: "/agents/skills",
  tools: "/agents/tools",
  models: "/models",
  functions: "/functions",
  workloads: "/workloads",
};

/**
 * `Agents ▾ › Skills` on the list, `Agents ▾ › Skills › name` on a detail,
 * and one more crumb for a flow under the detail. The first crumb is the
 * rail's switcher, so the two always agree (§4.4 rules 3 and 7).
 */
export function agentsCrumbs(fn: AgentsFunction, ...tail: Crumb[]): Crumb[] {
  const area = areaSwitcher(NAV, "agents", fn);
  const self: Crumb = tail.length > 0 ? { label: LABEL[fn], href: HREF[fn] } : { label: LABEL[fn] };
  return [area, self, ...tail];
}

/** The list state a numbered list hook reads. */
export interface NumberedListQuery {
  q: string;
  filters: Record<string, string>;
  sort: SortState[];
  page: number;
  pageSize: number;
}

export interface NumberedPageVariables<F> {
  search: string | null;
  filter: F | null;
  sort: string;
  page: number;
  pageSize: number;
}

/**
 * The list state as a numbered page's variables: `filter` is what the list
 * built from its chips and view (an empty one is `null`), `sort` is always
 * sent, which selects numbered paging on the server, falling back to the
 * list's default when the person cleared it.
 */
export function numberedPageVariables<F extends object>(
  { q, sort, page, pageSize }: NumberedListQuery,
  filter: F,
  defaultSort: SortState[]
): NumberedPageVariables<F> {
  return {
    search: q.trim() || null,
    filter: Object.keys(filter).length ? filter : null,
    sort: formatSort(sort.length ? sort : defaultSort),
    page: Math.max(1, page),
    pageSize,
  };
}

/** The note every client-paged view carries until its field takes the §5.1 arguments. */
export function clientNote(what: string): string {
  return `Filtered, sorted and paged in the browser: ${what}.`;
}

/** All is the whole set; its note says where the filtering happens. */
export function withAllNote(views: ListView[], note: string): ListView[] {
  return views.map((v) => (v.key === "all" ? { ...v, note } : v));
}

export interface SelectSpec<T> {
  /** True when the row passes the view's filters and the person's chips. */
  matches: (row: T, filters: Record<string, string>) => boolean;
  /** The text the search box matches, lower-cased by the caller. */
  text: (row: T) => (string | null | undefined)[];
  /** Sort values by key; an unknown key is skipped. */
  sortValue: Record<string, (row: T) => string | number>;
  /** The stable tie-break, so a page never reshuffles between renders. */
  id: (row: T) => string;
}

/**
 * Filter, search, sort and slice one numbered page. `totalCount` is the
 * filtered count, for "1–25 of 140".
 */
export function selectPage<T>(
  rows: T[],
  spec: SelectSpec<T>,
  {
    filters,
    q,
    sort,
    page,
    pageSize,
  }: {
    filters: Record<string, string>;
    q: string;
    sort: SortState[];
    page: number;
    pageSize: number;
  }
): { rows: T[]; totalCount: number } {
  const needle = q.trim().toLowerCase();
  const kept = rows.filter(
    (r) =>
      spec.matches(r, filters) &&
      (!needle || spec.text(r).some((f) => (f ?? "").toLowerCase().includes(needle)))
  );
  const sorted = [...kept].sort((a, b) => {
    for (const s of sort) {
      const value = spec.sortValue[s.key];
      if (!value) continue;
      const x = value(a);
      const y = value(b);
      if (x < y) return s.dir === "asc" ? -1 : 1;
      if (x > y) return s.dir === "asc" ? 1 : -1;
    }
    const x = spec.id(a);
    const y = spec.id(b);
    return x < y ? -1 : x > y ? 1 : 0;
  });
  const start = (Math.max(1, page) - 1) * pageSize;
  return { rows: sorted.slice(start, start + pageSize), totalCount: kept.length };
}

/** Milliseconds for a sort, 0 for a missing time. */
export const time = (ts: string | null | undefined) => (ts ? Date.parse(ts) || 0 : 0);

export const lower = (s: string | null | undefined) => (s ?? "").toLowerCase();

/** Errors a create or edit flow shows in place (spec 44 §5.4), plus one for the form. */
export type FieldErrors<F extends string> = Partial<Record<F, string>> & { form?: string };

const norm = (s: string) => s.replace(/[_\s-]/g, "").toLowerCase();

/**
 * A mutation's `errors { field message }` beside the fields they name,
 * matched across snake and camel case (`handler_ref` is `handlerRef`). An
 * error on no known field leads the form.
 */
export function splitErrors<F extends string>(
  errors: { field?: string | null; message: string }[],
  fields: readonly F[]
): FieldErrors<F> {
  const out: Record<string, string> = {};
  for (const err of errors) {
    const key = fields.find((f) => norm(f) === norm(err.field ?? "")) ?? "form";
    out[key] = out[key] ? `${out[key]} ${err.message}` : err.message;
  }
  return out as FieldErrors<F>;
}

export const hasErrors = (e: Record<string, string | undefined>) => Object.values(e).some(Boolean);
