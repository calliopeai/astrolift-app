"use client";

import { AlertTriangleIcon, CheckCircle2Icon, SearchIcon, XCircleIcon } from "lucide-react";
import * as React from "react";

import { type Crumb, ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import {
  canBindAt,
  describeSource,
  type GrantSourceInfo,
  type Principal,
  type RoleRef,
  SCOPE_NOUN,
  type ScopeNode,
  type ScopeRef,
  summarizePermissions,
} from "./access-model";
import { GrantSource } from "./GrantSource";
import { PrincipalChip } from "./PrincipalChip";
import { RoleSummary } from "./RoleSummary";
import { ScopePicker } from "./ScopePicker";

// ---------------------------------------------------------------------------
// The draft, the preview and the outcome
// ---------------------------------------------------------------------------

export type Expiry =
  | { kind: "never" }
  | { kind: "days"; days: number }
  | { kind: "date"; date: string };

export interface GrantDraft {
  principals: Principal[];
  roleId: string | null;
  scope: ScopeRef | null;
  expiry: Expiry;
}

/** What a grant would change (design 3.4, step 5), from `astroliftGrantPreview`. */
export interface GrantPreview {
  /** People who get something new, and what. A team or group counts its members. */
  gaining: { principal: Principal; permissions: string[] }[];
  /** People who already hold all of it, and from where. */
  already: { principal: Principal; source: GrantSourceInfo }[];
  /** How many gain and how many already had it, when the lists are cut short. */
  gainingCount?: number;
  alreadyCount?: number;
  /** IdP groups the grant reaches, and how many members each has today. */
  groups?: { groupExternalId: string; memberCount: number }[];
  /** Set when the caller's grant ceiling refuses it: nothing would be written. */
  refusal?: string | null;
  /** The server's caveats (a group's future members, an expiry), in words. */
  notes?: string[];
}

/**
 * An expiry as the `expiresAt` the backend takes: `days` from `now`, or the
 * end of a picked day (local time). Never is `null`.
 */
export function expiryToIso(e: Expiry, now: number): string | null {
  if (e.kind === "never") return null;
  if (e.kind === "days") return new Date(now + e.days * 86_400_000).toISOString();
  const end = new Date(`${e.date}T23:59:59`);
  return Number.isNaN(end.getTime()) ? null : end.toISOString();
}

export interface GrantOutcome {
  principal: Principal;
  ok: boolean;
  error?: string;
}

/** The principal search box, driven by the hook (debounced there). */
export interface PrincipalSearch {
  query: string;
  setQuery: (q: string) => void;
  results: Principal[];
  loading?: boolean;
  error?: { message: string } | null;
}

export interface GrantAccessFlowProps {
  crumbs: Crumb[];
  search: PrincipalSearch;
  roles: RoleRef[];
  rolesLoading?: boolean;
  rolesError?: { message: string } | null;
  scopeTree: {
    roots: ScopeNode[];
    loading?: boolean;
    error?: { message: string } | null;
    onRetry?: () => void;
    loadChildren?: (node: ScopeNode) => Promise<ScopeNode[]>;
  };
  /** Called on entering the review; the review shows it once it resolves. */
  preview: (draft: GrantDraft) => Promise<GrantPreview>;
  /** One outcome per principal; the flow shows failures in place. */
  onSubmit: (draft: GrantDraft) => Promise<GrantOutcome[]>;
  onCancel: () => void;
  /** After a submit where every principal succeeded. */
  onDone: (outcomes: GrantOutcome[]) => void;
  /** Whether an expiry can be set; without it only Never is offered. */
  expirySupported?: boolean;
  /** Seeds the draft: the current entity as the scope, a person from a row action. */
  initialDraft?: Partial<GrantDraft>;
  /** Opens on this step; stories use it to show each one. */
  initialStep?: Step;
  /** Stories: the preview already resolved. */
  initialPreview?: GrantPreview | null;
  /** Stories: the outcomes of a submit already made. */
  initialOutcomes?: GrantOutcome[] | null;
}

type Step = 0 | 1 | 2 | 3 | 4;
const STEPS = ["Who", "Role", "Scope", "Expiry", "Review"] as const;
const EXPIRY_DAYS = [1, 7, 30, 90];

const EMPTY: GrantDraft = { principals: [], roleId: null, scope: null, expiry: { kind: "never" } };

function expiryLabel(e: Expiry): string {
  if (e.kind === "never") return "never expires";
  if (e.kind === "days") return `expires in ${e.days} day${e.days === 1 ? "" : "s"}`;
  return `expires on ${e.date}`;
}

const principalKey = (p: Principal) => `${p.kind}:${p.id}`;

/**
 * The one way to grant access, from every place (design 3.4). Who (users,
 * groups, teams, many at once) › Role (each with what it allows in words) ›
 * Scope (the tree, the current entity preselected, levels the role cannot
 * bind at disabled with the reason) › Expiry › Review, which states the
 * effect before anything is written: who gains what, who already had it and
 * through what.
 *
 * Four fields and a review, so by spec 44 §5.4 this is a page with numbered
 * steps, not a sheet: the route is `/administration/access/grant?scope=…&
 * principal=…&return=…`, which any entity's Access tab links to. Errors sit
 * beside their field; a partly failed submit lists who failed and why, in
 * place, and keeps the rest. Pure: data and actions come from
 * `useGrantAccess`.
 */
export function GrantAccessFlow({
  crumbs,
  search,
  roles,
  rolesLoading = false,
  rolesError,
  scopeTree,
  preview,
  onSubmit,
  onCancel,
  onDone,
  expirySupported = false,
  initialDraft,
  initialStep = 0,
  initialPreview = null,
  initialOutcomes = null,
}: GrantAccessFlowProps) {
  const [step, setStep] = React.useState<Step>(initialStep);
  const [draft, setDraft] = React.useState<GrantDraft>({ ...EMPTY, ...initialDraft });
  const [error, setError] = React.useState<string | null>(null);
  const [previewState, setPreviewState] = React.useState<{
    data: GrantPreview | null;
    loading: boolean;
    error: string | null;
  }>({ data: initialPreview, loading: false, error: null });
  const [outcomes, setOutcomes] = React.useState<GrantOutcome[] | null>(initialOutcomes);
  const [submitting, setSubmitting] = React.useState(false);

  const role = roles.find((r) => r.id === draft.roleId) ?? null;
  const patch = (next: Partial<GrantDraft>) => {
    setDraft((d) => ({ ...d, ...next }));
    setError(null);
  };

  function problem(at: Step): string | null {
    if (at === 0 && draft.principals.length === 0)
      return "Pick at least one person, group or team.";
    if (at === 1 && !role) return "Pick a role.";
    if (at === 2) {
      if (!draft.scope) return "Pick where the role applies.";
      if (role) {
        const ok = canBindAt(role, draft.scope.kind);
        if (ok !== true) return ok;
      }
    }
    if (at === 3 && draft.expiry.kind === "date" && !draft.expiry.date) return "Pick a date.";
    return null;
  }

  async function loadPreview(d: GrantDraft) {
    setPreviewState({ data: null, loading: true, error: null });
    try {
      setPreviewState({ data: await preview(d), loading: false, error: null });
    } catch (err) {
      setPreviewState({
        data: null,
        loading: false,
        error: err instanceof Error ? err.message : "Could not preview",
      });
    }
  }

  function goTo(target: Step) {
    // Forward checks every step in between; back never blocks.
    for (let s = step; s < target; s++) {
      const p = problem(s as Step);
      if (p) {
        setStep(s as Step);
        setError(p);
        return;
      }
    }
    setError(null);
    setStep(target);
    if (target === 4) void loadPreview(draft);
  }

  async function submit() {
    setSubmitting(true);
    // A retry sends only who failed; who succeeded stays succeeded.
    const done = outcomes?.filter((o) => o.ok) ?? [];
    const retrying = outcomes?.filter((o) => !o.ok).map((o) => o.principal);
    try {
      const result = [
        ...done,
        ...(await onSubmit(retrying?.length ? { ...draft, principals: retrying } : draft)),
      ];
      setOutcomes(result);
      if (result.every((o) => o.ok)) onDone(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The grant failed");
    } finally {
      setSubmitting(false);
    }
  }

  const failed = outcomes?.filter((o) => !o.ok) ?? [];
  const succeeded = outcomes?.filter((o) => o.ok) ?? [];

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
      <ShellHeader crumbs={crumbs} title="Grant access" />

      <ol aria-label="Steps" className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
        {STEPS.map((label, i) => (
          <li key={label} className="min-w-0">
            <button
              type="button"
              onClick={() => (i < step ? setStep(i as Step) : goTo(i as Step))}
              aria-current={i === step ? "step" : undefined}
              className={cn(
                "focus-visible:ring-ring inline-flex items-center gap-2 rounded-sm text-sm focus-visible:ring-2 focus-visible:outline-none",
                i === step ? "text-foreground font-medium" : "text-muted-foreground"
              )}
            >
              <span
                className={cn(
                  "inline-flex size-5 items-center justify-center rounded-full border font-mono text-xs",
                  i === step && "border-primary text-primary",
                  i < step && "bg-muted"
                )}
              >
                {i + 1}
              </span>
              {label}
            </button>
          </li>
        ))}
      </ol>

      <div className="bg-card flex max-w-3xl min-w-0 flex-col gap-5 rounded-md border p-5">
        {step === 0 && <WhoStep draft={draft} patch={patch} search={search} />}

        {step === 1 && (
          <RoleStep
            roles={roles}
            loading={rolesLoading}
            error={rolesError}
            value={draft.roleId}
            onChange={(roleId) => patch({ roleId })}
          />
        )}

        {step === 2 && (
          <div className="flex min-w-0 flex-col gap-2">
            <h2 className="text-sm font-medium">Where does it apply?</h2>
            <p className="text-muted-foreground text-sm">
              A grant reaches everything inside the scope: a team grant covers its projects and
              apps.
            </p>
            <ScopePicker
              roots={scopeTree.roots}
              loading={scopeTree.loading}
              error={scopeTree.error}
              onRetry={scopeTree.onRetry}
              loadChildren={scopeTree.loadChildren}
              value={draft.scope}
              onChange={(n) => patch({ scope: { kind: n.kind, id: n.id, name: n.name } })}
              selectable={role ? (n) => canBindAt(role, n.kind) : undefined}
            />
          </div>
        )}

        {step === 3 && (
          <fieldset className="flex min-w-0 flex-col gap-3">
            <legend className="mb-2 text-sm font-medium">When does it end?</legend>
            <div className="flex min-w-0 flex-wrap gap-2">
              <ExpiryOption
                label="Never"
                on={draft.expiry.kind === "never"}
                onClick={() => patch({ expiry: { kind: "never" } })}
              />
              {EXPIRY_DAYS.map((days) => (
                <ExpiryOption
                  key={days}
                  label={`${days} day${days === 1 ? "" : "s"}`}
                  on={draft.expiry.kind === "days" && draft.expiry.days === days}
                  disabled={!expirySupported}
                  onClick={() => patch({ expiry: { kind: "days", days } })}
                />
              ))}
              <ExpiryOption
                label="On a date"
                on={draft.expiry.kind === "date"}
                disabled={!expirySupported}
                onClick={() => patch({ expiry: { kind: "date", date: "" } })}
              />
            </div>
            {draft.expiry.kind === "date" && (
              <Input
                type="date"
                aria-label="Expiry date"
                value={draft.expiry.date}
                onChange={(e) => patch({ expiry: { kind: "date", date: e.target.value } })}
                className="w-48 font-mono"
              />
            )}
            {!expirySupported ? (
              <p className="text-muted-foreground text-xs">
                This grant cannot be time-boxed here; it lasts until it is removed.
              </p>
            ) : (
              draft.expiry.kind !== "never" && (
                <p className="text-muted-foreground text-xs">
                  The grant ends by itself then; removing it earlier still works.
                </p>
              )
            )}
          </fieldset>
        )}

        {step === 4 && (
          <Review
            draft={draft}
            role={role}
            preview={previewState}
            onRetryPreview={() => void loadPreview(draft)}
            succeeded={succeeded}
            failed={failed}
          />
        )}

        {error && (
          <p role="alert" className="text-danger-fg text-sm [overflow-wrap:anywhere]">
            {error}
          </p>
        )}

        <div className="flex flex-wrap justify-end gap-2 border-t pt-4">
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          {step > 0 && (
            <Button type="button" variant="outline" onClick={() => setStep((step - 1) as Step)}>
              Back
            </Button>
          )}
          {step < 4 ? (
            <Button type="button" onClick={() => goTo((step + 1) as Step)}>
              Continue
            </Button>
          ) : (
            <Button
              type="button"
              onClick={() => void submit()}
              disabled={submitting || previewState.loading || Boolean(previewState.data?.refusal)}
            >
              {submitting
                ? "Granting…"
                : failed.length > 0
                  ? `Retry ${failed.length}`
                  : `Grant to ${draft.principals.length}`}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

function WhoStep({
  draft,
  patch,
  search,
}: {
  draft: GrantDraft;
  patch: (next: Partial<GrantDraft>) => void;
  search: PrincipalSearch;
}) {
  const picked = new Set(draft.principals.map(principalKey));
  const results = search.results.filter((p) => !picked.has(principalKey(p)));
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <h2 className="text-sm font-medium">Who gets access?</h2>
      {draft.principals.length > 0 && (
        <ul aria-label="Picked" className="flex min-w-0 flex-wrap gap-2">
          {draft.principals.map((p) => (
            <li key={principalKey(p)} className="max-w-full min-w-0">
              <PrincipalChip
                principal={p}
                onRemove={() =>
                  patch({
                    principals: draft.principals.filter((x) => principalKey(x) !== principalKey(p)),
                  })
                }
              />
            </li>
          ))}
        </ul>
      )}
      <div className="relative min-w-0">
        <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
        <Input
          type="search"
          value={search.query}
          onChange={(e) => search.setQuery(e.target.value)}
          placeholder="Search people, groups and teams by name or email"
          aria-label="Search people, groups and teams"
          className="pl-8"
        />
      </div>
      <div className="max-h-72 min-w-0 overflow-auto rounded-md border">
        {search.error ? (
          <p role="alert" className="text-danger-fg p-3 text-sm [overflow-wrap:anywhere]">
            Search failed: {search.error.message}
          </p>
        ) : search.loading ? (
          <div className="flex flex-col gap-2 p-3" aria-busy>
            {Array.from({ length: 3 }, (_, i) => (
              <Skeleton key={i} className="h-8" />
            ))}
          </div>
        ) : results.length === 0 ? (
          <p className="text-muted-foreground p-3 text-sm">
            {search.query.trim()
              ? `No one matches "${search.query.trim()}".`
              : "Type to search. A group grant reaches whoever is in the group, now and later; a team adds its members at the time of the grant."}
          </p>
        ) : (
          <ul className="divide-y">
            {results.map((p) => (
              <li key={principalKey(p)} className="flex min-w-0 items-center gap-2 px-3 py-2">
                <PrincipalChip
                  principal={{ ...p, href: undefined }}
                  variant="block"
                  className="flex-1"
                />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => patch({ principals: [...draft.principals, p] })}
                  aria-label={`Add ${p.name}`}
                >
                  Add
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function RoleStep({
  roles,
  loading,
  error,
  value,
  onChange,
}: {
  roles: RoleRef[];
  loading: boolean;
  error?: { message: string } | null;
  value: string | null;
  onChange: (id: string) => void;
}) {
  const [q, setQ] = React.useState("");
  const needle = q.trim().toLowerCase();
  const shown = needle
    ? roles.filter(
        (r) =>
          r.name.toLowerCase().includes(needle) ||
          r.slug.toLowerCase().includes(needle) ||
          r.permissions.some((p) => p.includes(needle))
      )
    : roles;
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <h2 className="text-sm font-medium">Which role?</h2>
      <Input
        type="search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search roles or a permission, e.g. deploy"
        aria-label="Search roles"
      />
      <div className="max-h-96 min-w-0 overflow-auto rounded-md border">
        {error ? (
          <p role="alert" className="text-danger-fg p-3 text-sm [overflow-wrap:anywhere]">
            Could not load roles: {error.message}
          </p>
        ) : loading ? (
          <div className="flex flex-col gap-2 p-3" aria-busy>
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="h-12" />
            ))}
          </div>
        ) : shown.length === 0 ? (
          <p className="text-muted-foreground p-3 text-sm">
            {roles.length === 0
              ? "You cannot grant any role: it takes holding every permission a role carries."
              : `No role matches "${q.trim()}".`}
          </p>
        ) : (
          <div role="radiogroup" aria-label="Role" className="divide-y">
            {shown.map((r) => {
              const on = r.id === value;
              return (
                <div
                  key={r.id}
                  className={cn("flex min-w-0 items-start gap-3 px-3 py-3", on && "bg-primary/5")}
                >
                  <button
                    type="button"
                    role="radio"
                    aria-checked={on}
                    aria-label={r.name}
                    onClick={() => onChange(r.id)}
                    className={cn(
                      "focus-visible:ring-ring mt-1 size-4 shrink-0 rounded-full border focus-visible:ring-2 focus-visible:outline-none",
                      on && "border-primary bg-primary ring-primary/30 ring-2"
                    )}
                  />
                  <RoleSummary role={r} className="flex-1" />
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function ExpiryOption({
  label,
  on,
  disabled,
  onClick,
}: {
  label: string;
  on: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant={on ? "default" : "outline"}
      aria-pressed={on}
      disabled={disabled}
      onClick={onClick}
    >
      {label}
    </Button>
  );
}

function Review({
  draft,
  role,
  preview,
  onRetryPreview,
  succeeded,
  failed,
}: {
  draft: GrantDraft;
  role: RoleRef | null;
  preview: { data: GrantPreview | null; loading: boolean; error: string | null };
  onRetryPreview: () => void;
  succeeded: GrantOutcome[];
  failed: GrantOutcome[];
}) {
  const p = preview.data;
  const scope = draft.scope;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <h2 className="text-sm font-medium">Review</h2>
      <p className="min-w-0 text-sm [overflow-wrap:anywhere]">
        <span className="font-medium">{role?.name ?? "No role"}</span>{" "}
        <span className="text-muted-foreground">
          ({role ? summarizePermissions(role.permissions) : "none"})
        </span>{" "}
        on {scope ? SCOPE_NOUN[scope.kind] : "no scope"}{" "}
        <span className="font-mono">{scope?.name ?? ""}</span>, {expiryLabel(draft.expiry)}.
      </p>

      {preview.loading ? (
        <div className="flex flex-col gap-2" aria-busy>
          <Skeleton className="h-6 w-2/3" />
          <Skeleton className="h-20" />
        </div>
      ) : preview.error ? (
        <div
          role="alert"
          className="border-warning-border bg-warning-bg text-warning-fg flex min-w-0 flex-wrap items-center gap-2 rounded-md border p-3 text-sm"
        >
          <AlertTriangleIcon className="size-4 shrink-0" />
          <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
            Could not preview the effect: {preview.error}
          </span>
          <Button size="sm" variant="outline" onClick={onRetryPreview}>
            Retry
          </Button>
        </div>
      ) : p ? (
        <>
          <p className="text-sm font-medium [overflow-wrap:anywhere]" data-testid="grant-effect">
            {effectSentence(p, role, scope)}
          </p>
          {p.refusal && (
            <p
              role="alert"
              className="border-danger-border bg-danger-bg text-danger-fg min-w-0 rounded-md border p-3 text-sm [overflow-wrap:anywhere]"
            >
              {p.refusal}
            </p>
          )}
          {p.groups && p.groups.length > 0 && (
            <p className="text-muted-foreground text-xs [overflow-wrap:anywhere]">
              Reaches{" "}
              {p.groups.map((g) => `${g.groupExternalId} (${g.memberCount} today)`).join(", ")}, and
              whoever the identity provider adds to {p.groups.length === 1 ? "it" : "them"} later.
            </p>
          )}
          {p.notes?.map((n) => (
            <p key={n} className="text-muted-foreground text-xs [overflow-wrap:anywhere]">
              {n}
            </p>
          ))}
          {p.gaining.length > 0 && (
            <section className="flex min-w-0 flex-col gap-2">
              <h3 className="text-muted-foreground text-xs font-medium uppercase">Gain access</h3>
              <ul className="max-h-56 min-w-0 divide-y overflow-auto rounded-md border">
                {p.gaining.map((g) => (
                  <li
                    key={principalKey(g.principal)}
                    className="flex min-w-0 flex-col gap-1 px-3 py-2"
                  >
                    <PrincipalChip principal={g.principal} />
                    <span className="text-muted-foreground text-2xs min-w-0 font-mono [overflow-wrap:anywhere]">
                      + {g.permissions.join(", ")}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {p.already.length > 0 && (
            <section className="flex min-w-0 flex-col gap-2">
              <h3 className="text-muted-foreground text-xs font-medium uppercase">
                Already have it
              </h3>
              <ul className="max-h-40 min-w-0 divide-y overflow-auto rounded-md border">
                {p.already.map((a) => (
                  <li
                    key={principalKey(a.principal)}
                    className="flex min-w-0 flex-wrap items-center gap-2 px-3 py-2"
                    title={describeSource(a.source)}
                  >
                    <PrincipalChip principal={a.principal} />
                    <GrantSource source={a.source} />
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      ) : null}

      {(succeeded.length > 0 || failed.length > 0) && (
        <section aria-label="Outcome" className="flex min-w-0 flex-col gap-2">
          {succeeded.length > 0 && (
            <p className="text-success-fg flex items-center gap-2 text-sm">
              <CheckCircle2Icon className="size-4 shrink-0" />
              Granted to {succeeded.length}.
            </p>
          )}
          {failed.length > 0 && (
            <ul className="border-danger-border min-w-0 divide-y rounded-md border">
              {failed.map((f) => (
                <li
                  key={principalKey(f.principal)}
                  className="flex min-w-0 flex-col gap-1 px-3 py-2"
                >
                  <span className="text-danger-fg flex min-w-0 items-center gap-2 text-sm">
                    <XCircleIcon className="size-4 shrink-0" />
                    <PrincipalChip principal={f.principal} />
                  </span>
                  <span className="text-danger-fg min-w-0 text-xs [overflow-wrap:anywhere]">
                    {f.error ?? "Failed"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}

/** "14 people gain access to app checkout; 3 already had it." */
export function effectSentence(
  p: GrantPreview,
  role: RoleRef | null,
  scope: ScopeRef | null
): string {
  const n = p.gainingCount ?? p.gaining.length;
  const already = p.alreadyCount ?? p.already.length;
  const where = scope ? ` on ${SCOPE_NOUN[scope.kind]} ${scope.name}` : "";
  const what = role ? ` ${role.name}` : " access";
  const lead =
    n === 0
      ? `No one gains anything new${where}`
      : `${n} ${n === 1 ? "person gains" : "people gain"}${what}${where}`;
  const tail = already ? `; ${already} already had it` : "";
  return `${lead}${tail}.`;
}
