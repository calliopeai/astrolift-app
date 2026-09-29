"use client";

import { SearchXIcon } from "lucide-react";
import * as React from "react";

import { SCOPE_NOUN, SCOPE_ORDER } from "@/components/access/access-model";
import {
  conditionError,
  parsePolicy,
  type PolicyShape,
  policySentence,
  serializePolicy,
} from "@/components/access/policy-model";
import { PolicyConditionHelp } from "@/components/access/PolicyConditionHelp";
import { PolicySentence } from "@/components/access/PolicySentence";
import {
  type PolicySimulation,
  PolicySimulationPanel,
} from "@/components/access/PolicySimulationPanel";
import { EmptyState } from "@/components/EmptyState";
import { Panel } from "@/components/panel/Panel";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import type { ScopeKind } from "@/graphql/identity/identity.types";
import { cn } from "@/lib/utils";

import { PEOPLE_HREF } from "./access-nav";
import { POLICIES_HREF, policiesCrumbs } from "./policy-routes";
import type { PolicyInput, usePolicyEditor } from "./use-policy-editor";

type Step = 0 | 1 | 2;
const STEPS = ["Details", "Rule", "Review"] as const;

export interface PolicyDraft {
  name: string;
  slug: string;
  slugTouched: boolean;
  description: string;
  scopeLevel: ScopeKind;
  shape: PolicyShape;
}

export type PolicyEditorScreenProps = ReturnType<typeof usePolicyEditor> & {
  /** Stories: open on this step. */
  initialStep?: Step;
  /** Stories: seed the draft. */
  initialDraft?: Partial<PolicyDraft>;
  /** Stories: the review's simulation already answered. */
  initialSimulation?: PolicySimulation | null;
};

const slugify = (s: string) =>
  s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);

const EMPTY_SHAPE: PolicyShape = {
  effect: "DENY",
  actionPattern: "app.deploy",
  resource: {},
  conditions: [
    {
      kind: "time_window",
      days: ["mon", "tue", "wed", "thu", "fri"],
      hours: ["09:00-18:00"],
      tz: "UTC",
    },
  ],
  actor: { groups: [], role: "" },
};

function draftOf(policy: PolicyEditorScreenProps["policy"]): PolicyDraft {
  if (!policy) {
    return {
      name: "",
      slug: "",
      slugTouched: false,
      description: "",
      scopeLevel: "ORG",
      shape: EMPTY_SHAPE,
    };
  }
  return {
    name: policy.name,
    slug: policy.slug,
    slugTouched: true,
    description: policy.description ?? "",
    scopeLevel: policy.scopeLevel,
    shape: parsePolicy(policy),
  };
}

/**
 * New policy and a policy's own page (design 3.6, spec 44 §5.4): six
 * fields, so a page with numbered steps. Details, then the rule built as a
 * sentence (`PolicySentence` in edit mode: action, resource, actors and one
 * picker per condition, raw JSON one click away), then a review that states
 * the rule in words and runs it through the server's simulation (today's
 * holders, the last week of recorded decisions) before anything is written.
 * The resolver enforces policies, and a condition the request cannot answer
 * denies; the rule step says, from the server's catalog, what each
 * condition needs. A viewer without `org.update` reads the policy as its
 * sentence. Pure.
 */
export function PolicyEditorScreen(props: PolicyEditorScreenProps) {
  const { mode, id, policy, loading, error, onRetry, canManage } = props;
  const title = mode === "create" ? "New policy" : (policy?.name ?? "Policy");

  if (mode === "edit" && !policy) {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
        <ShellHeader
          crumbs={policiesCrumbs(loading ? "Policy" : "Not found")}
          title={loading ? <Skeleton className="h-6 w-56" /> : "Policy not found"}
        />
        {loading ? (
          <Panel title="Rule" loading />
        ) : error ? (
          <Panel title="Policy" error={error} onRetry={onRetry} />
        ) : (
          <EmptyState
            icon={<SearchXIcon className="size-5" />}
            title="No policy with this id"
            description={`Nothing in this organization has the id ${id}. It may have been deleted.`}
            actionHref={POLICIES_HREF}
            actionLabel="All policies"
          />
        )}
      </div>
    );
  }

  if (mode === "edit" && policy && !canManage) {
    const shape = parsePolicy(policy);
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
        <ShellHeader crumbs={policiesCrumbs(policy.name)} title={policy.name} />
        <div className="bg-card flex max-w-3xl min-w-0 flex-col gap-4 rounded-md border p-5">
          <PolicySentence policy={shape} className="text-base" />
          <Summary draft={draftOf(policy)} />
          <p className="text-muted-foreground text-xs">
            Changing a policy needs <span className="font-mono">org.update</span>.
          </p>
        </div>
      </div>
    );
  }

  return <Editor key={policy?.id ?? "new"} {...props} title={title} />;
}

function Editor({
  mode,
  policy,
  saving,
  onSave,
  onCancel,
  catalog,
  simulate,
  initialStep = 0,
  initialDraft,
  initialSimulation = null,
  title,
}: PolicyEditorScreenProps & { title: string }) {
  const [step, setStep] = React.useState<Step>(initialStep);
  const [draft, setDraft] = React.useState<PolicyDraft>({ ...draftOf(policy), ...initialDraft });
  const [errors, setErrors] = React.useState<Partial<Record<"name" | "slug" | "rule", string>>>({});
  const [submitError, setSubmitError] = React.useState<string | null>(null);
  const [sim, setSim] = React.useState<{
    data: PolicySimulation | null;
    loading: boolean;
    error: string | null;
  }>({ data: initialSimulation, loading: false, error: null });

  const patch = (next: Partial<PolicyDraft>) => setDraft((d) => ({ ...d, ...next }));
  const slug = draft.slugTouched ? draft.slug : slugify(draft.name);
  const was = policy ? parsePolicy(policy) : null;

  function inputOf(d: PolicyDraft): PolicyInput {
    return {
      name: d.name.trim(),
      slug,
      description: d.description.trim(),
      scopeLevel: d.scopeLevel,
      ...serializePolicy(d.shape),
    };
  }

  async function runSimulation(d: PolicyDraft) {
    setSim({ data: null, loading: true, error: null });
    try {
      setSim({ data: await simulate(inputOf(d)), loading: false, error: null });
    } catch (err) {
      setSim({
        data: null,
        loading: false,
        error: err instanceof Error ? err.message : "The simulation failed",
      });
    }
  }

  function validate(at: Step): boolean {
    const next: typeof errors = {};
    if (at === 0) {
      if (!draft.name.trim()) next.name = "Name the policy.";
      if (mode === "create" && !/^[a-z0-9-]+$/.test(slug))
        next.slug = "Lowercase letters, digits and hyphens only.";
    }
    if (at === 1) {
      const bad = draft.shape.conditions.map(conditionError).filter(Boolean);
      if (bad.length)
        next.rule = `Fix ${bad.length === 1 ? "the condition" : `${bad.length} conditions`} marked below first.`;
      else if (!draft.shape.actionPattern.trim())
        next.rule = "Name an action, or * for every action.";
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  function goTo(target: Step) {
    // Forward checks every step in between; back never blocks.
    for (let s = step; s < target; s++) {
      if (!validate(s as Step)) {
        setStep(s as Step);
        return;
      }
    }
    setStep(target);
    if (target === 2) void runSimulation(draft);
  }

  async function submit() {
    if (!validate(0)) return setStep(0);
    if (!validate(1)) return setStep(1);
    setSubmitError(await onSave(inputOf(draft)));
  }

  const changed = was !== null && policySentence(was) !== policySentence(draft.shape);

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
      <ShellHeader
        crumbs={policiesCrumbs(mode === "create" ? "New policy" : title)}
        title={title}
        context="A policy narrows what roles allow; it never grants more."
      />

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
                  i === step && "border-primary text-primary"
                )}
              >
                {i + 1}
              </span>
              {label}
            </button>
          </li>
        ))}
      </ol>

      <div className="bg-card flex max-w-4xl min-w-0 flex-col gap-5 rounded-md border p-5">
        {step === 0 && (
          <>
            <div className="grid min-w-0 gap-4 sm:grid-cols-2">
              <Field id="policy-name" label="Name" error={errors.name}>
                <Input
                  id="policy-name"
                  value={draft.name}
                  onChange={(e) => patch({ name: e.target.value })}
                  placeholder="No prod deploys after hours"
                  aria-invalid={Boolean(errors.name)}
                />
              </Field>
              <Field
                id="policy-slug"
                label="Slug"
                error={errors.slug}
                help={
                  mode === "create"
                    ? "Derived from the name until you edit it. Fixed once created."
                    : "Fixed once created."
                }
              >
                <Input
                  id="policy-slug"
                  value={slug}
                  onChange={(e) => patch({ slug: e.target.value, slugTouched: true })}
                  className="font-mono"
                  aria-invalid={Boolean(errors.slug)}
                  disabled={mode === "edit"}
                />
              </Field>
            </div>
            <Field
              id="policy-scope"
              label="Applies across"
              help={
                mode === "create"
                  ? "How widely the rule reaches. Fixed once created."
                  : "Fixed once created."
              }
            >
              <Select
                value={draft.scopeLevel}
                onValueChange={(v) => patch({ scopeLevel: v as ScopeKind })}
                disabled={mode === "edit"}
              >
                <SelectTrigger id="policy-scope" className="max-w-xs min-w-0">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SCOPE_ORDER.map((s) => (
                    <SelectItem key={s} value={s}>
                      {SCOPE_NOUN[s]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field id="policy-description" label="Description">
              <Textarea
                id="policy-description"
                value={draft.description}
                onChange={(e) => patch({ description: e.target.value })}
                placeholder="Why this rule exists, in one sentence."
                rows={2}
              />
            </Field>
          </>
        )}

        {step === 1 && (
          <>
            {errors.rule && (
              <p role="alert" className="text-danger text-sm [overflow-wrap:anywhere]">
                {errors.rule}
              </p>
            )}
            <PolicySentence policy={draft.shape} onChange={(shape) => patch({ shape })} />
            <PolicyConditionHelp
              catalog={catalog.conditions}
              kinds={draft.shape.conditions.map((c) => c.kind).filter((k) => k !== "custom")}
              loading={catalog.loading}
              error={catalog.error}
              className="border-t pt-4"
            />
          </>
        )}

        {step === 2 && (
          <>
            <section aria-label="The rule" className="flex min-w-0 flex-col gap-2">
              <PolicySentence policy={draft.shape} className="text-base" />
              {changed && was && (
                <p className="text-muted-foreground text-xs [overflow-wrap:anywhere]">
                  Was: {policySentence(was)}
                </p>
              )}
            </section>

            <p className="text-muted-foreground text-sm">
              Saving puts this rule in force: every permission check evaluates it from then on, and
              a condition the request cannot answer denies.
            </p>

            <PolicySimulationPanel
              simulation={sim.data}
              loading={sim.loading}
              error={sim.error}
              onRetry={() => void runSimulation(draft)}
              personHref={(memberId) => `${PEOPLE_HREF}/${memberId}`}
              className="border-t pt-4"
            />

            <Summary draft={{ ...draft, slug }} />

            {submitError && (
              <p role="alert" className="text-danger text-sm [overflow-wrap:anywhere]">
                {submitError}
              </p>
            )}
          </>
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
          {step < 2 ? (
            <Button type="button" onClick={() => goTo((step + 1) as Step)}>
              Continue
            </Button>
          ) : (
            <Button type="button" onClick={submit} disabled={saving}>
              {saving ? "Saving…" : mode === "create" ? "Create policy" : "Save policy"}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

function Summary({ draft }: { draft: PolicyDraft }) {
  return (
    <DefinitionList
      items={[
        {
          term: "Name",
          description: (
            <span className="[overflow-wrap:anywhere]">{draft.name.trim() || "none"}</span>
          ),
        },
        {
          term: "Slug",
          description: <span className="font-mono [overflow-wrap:anywhere]">{draft.slug}</span>,
        },
        { term: "Applies across", description: SCOPE_NOUN[draft.scopeLevel] },
        {
          term: "Conditions",
          description: (
            <span className="font-mono tabular-nums">{draft.shape.conditions.length}</span>
          ),
        },
      ]}
    />
  );
}

function Field({
  id,
  label,
  error,
  help,
  children,
}: {
  id: string;
  label: string;
  error?: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {error ? (
        <p className="text-danger text-xs [overflow-wrap:anywhere]">{error}</p>
      ) : help ? (
        <p className="text-muted-foreground text-xs">{help}</p>
      ) : null}
    </div>
  );
}
