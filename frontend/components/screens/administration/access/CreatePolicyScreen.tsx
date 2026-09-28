"use client";

import { AlertTriangleIcon } from "lucide-react";
import * as React from "react";

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
import { Textarea } from "@/components/ui/textarea";
import type { PolicyEffect, ScopeKind } from "@/graphql/identity/identity.types";
import { cn } from "@/lib/utils";

import { adminCrumb } from "./admin-crumbs";
import { type CreatePolicyInput, POLICIES_HREF, type useCreatePolicy } from "./use-create-policy";

export type CreatePolicyScreenProps = ReturnType<typeof useCreatePolicy> & {
  /** Opens on this step; stories use it to show each one. */
  initialStep?: Step;
  /** Seeds the draft; stories use it for filled and long states. */
  initialDraft?: Partial<Draft>;
};

type Step = 0 | 1 | 2;
const STEPS = ["Rule", "Conditions", "Review"] as const;

interface Draft {
  name: string;
  slug: string;
  slugTouched: boolean;
  scopeLevel: ScopeKind;
  effect: PolicyEffect;
  actionPattern: string;
  conditionsText: string;
}

const slugify = (s: string) =>
  s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);

const CONDITION_TEMPLATE = `[
  {
    "kind": "time_window",
    "days": ["mon","tue","wed","thu","fri"],
    "hours": ["09:00-18:00"],
    "tz": "America/Los_Angeles"
  }
]`;

const EMPTY_DRAFT: Draft = {
  name: "",
  slug: "",
  slugTouched: false,
  scopeLevel: "ORG",
  effect: "DENY",
  actionPattern: "*",
  conditionsText: CONDITION_TEMPLATE,
};

function parseConditions(text: string): { value: unknown[] } | { error: string } {
  try {
    const parsed: unknown = JSON.parse(text.trim() || "[]");
    if (!Array.isArray(parsed)) return { error: "conditions must be a JSON array" };
    return { value: parsed };
  } catch (err) {
    return { error: err instanceof Error ? err.message : "invalid JSON" };
  }
}

/**
 * Admin › Policies › New policy (spec 44 §5.4). Six fields, so a page with
 * numbered steps rather than a sheet: the rule, its conditions, then a review
 * before anything is written, because a DENY policy removes access.
 * Validation sits beside its field; a server refusal shows on the review step.
 */
export function CreatePolicyScreen({
  creating,
  createPolicy,
  onCancel,
  initialStep = 0,
  initialDraft,
}: CreatePolicyScreenProps) {
  const [step, setStep] = React.useState<Step>(initialStep);
  const [draft, setDraft] = React.useState<Draft>({ ...EMPTY_DRAFT, ...initialDraft });
  const [errors, setErrors] = React.useState<
    Partial<Record<"name" | "slug" | "conditions", string>>
  >({});
  const [submitError, setSubmitError] = React.useState<string | null>(null);

  const patch = (next: Partial<Draft>) => setDraft((d) => ({ ...d, ...next }));
  const slug = draft.slug || slugify(draft.name);

  function validate(at: Step): boolean {
    const next: typeof errors = {};
    if (at === 0) {
      if (!draft.name.trim()) next.name = "A display name is required.";
      if (!/^[a-z0-9-]+$/.test(slug)) next.slug = "Use lowercase letters, digits and hyphens only.";
    }
    if (at === 1) {
      const parsed = parseConditions(draft.conditionsText);
      if ("error" in parsed) next.conditions = parsed.error;
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  function goTo(target: Step) {
    // Moving forward checks every step in between; moving back never blocks.
    for (let s = step; s < target; s++) {
      if (!validate(s as Step)) {
        setStep(s as Step);
        return;
      }
    }
    setStep(target);
  }

  async function submit() {
    const parsed = parseConditions(draft.conditionsText);
    if ("error" in parsed) {
      setErrors({ conditions: parsed.error });
      setStep(1);
      return;
    }
    const input: CreatePolicyInput = {
      name: draft.name.trim(),
      slug,
      scopeLevel: draft.scopeLevel,
      effect: draft.effect,
      actionPattern: draft.actionPattern.trim() || "*",
      conditions: parsed.value,
    };
    setSubmitError(await createPolicy(input));
  }

  const parsedForReview = parseConditions(draft.conditionsText);
  const conditionCount = "value" in parsedForReview ? parsedForReview.value.length : 0;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
      <ShellHeader
        crumbs={[
          adminCrumb("policies"),
          { label: "Policies", href: POLICIES_HREF },
          { label: "New policy" },
        ]}
        title="New policy"
        context="ABAC policies can only deny."
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

      <div className="bg-card flex max-w-3xl min-w-0 flex-col gap-5 rounded-md border p-5">
        {step === 0 && (
          <>
            <Field id="name" label="Display name" error={errors.name}>
              <Input
                id="name"
                value={draft.name}
                onChange={(e) => patch({ name: e.target.value })}
                placeholder="No prod deploys after hours"
                aria-invalid={Boolean(errors.name)}
                autoFocus
              />
            </Field>
            <Field
              id="slug"
              label="Slug"
              error={errors.slug}
              help="Derived from the name until you edit it."
            >
              <Input
                id="slug"
                value={draft.slugTouched ? draft.slug : slug}
                onChange={(e) => patch({ slug: e.target.value, slugTouched: true })}
                className="font-mono"
                aria-invalid={Boolean(errors.slug)}
              />
            </Field>
            <div className="grid min-w-0 gap-4 sm:grid-cols-3">
              <Field id="scope" label="Scope">
                <Select
                  value={draft.scopeLevel}
                  onValueChange={(v) => patch({ scopeLevel: v as ScopeKind })}
                >
                  <SelectTrigger id="scope" className="font-mono">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="ORG">ORG</SelectItem>
                    <SelectItem value="TEAM">TEAM</SelectItem>
                    <SelectItem value="PROJECT">PROJECT</SelectItem>
                    <SelectItem value="APP">APP</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field id="effect" label="Effect">
                <Select
                  value={draft.effect}
                  onValueChange={(v) => patch({ effect: v as PolicyEffect })}
                >
                  <SelectTrigger id="effect" className="font-mono">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="DENY">DENY</SelectItem>
                    <SelectItem value="ALLOW">ALLOW</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field id="action" label="Action pattern">
                <Input
                  id="action"
                  value={draft.actionPattern}
                  onChange={(e) => patch({ actionPattern: e.target.value })}
                  placeholder="app.deploy"
                  className="font-mono text-xs"
                />
              </Field>
            </div>
            <p className="text-muted-foreground text-xs">
              Scope sets how widely the rule applies. Effect is normally{" "}
              <span className="font-mono">DENY</span> — ABAC narrows access, it doesn&apos;t grant
              it. Action pattern is a permission glob like{" "}
              <span className="font-mono">app.deploy</span> or <span className="font-mono">*</span>{" "}
              for every action.
            </p>
          </>
        )}

        {step === 1 && (
          <Field
            id="conditions"
            label="Conditions (JSON array)"
            error={errors.conditions}
            help="Predicate kinds per spec/03 §5: time_window, ip_allowlist, approval_required, env_match, device_assertion, freshness."
          >
            <Textarea
              id="conditions"
              value={draft.conditionsText}
              onChange={(e) => patch({ conditionsText: e.target.value })}
              rows={12}
              className="font-mono text-xs"
              aria-invalid={Boolean(errors.conditions)}
            />
          </Field>
        )}

        {step === 2 && (
          <>
            {draft.effect === "DENY" && (
              <div className="border-warning-border bg-warning/10 text-warning-fg flex min-w-0 items-start gap-2 rounded-md border p-3 text-sm">
                <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
                <p className="min-w-0">
                  This DENY applies on the next request. Members whose roles allow{" "}
                  <span className="font-mono [overflow-wrap:anywhere]">
                    {draft.actionPattern.trim() || "*"}
                  </span>{" "}
                  lose it wherever its conditions match.
                </p>
              </div>
            )}
            <DefinitionList
              items={[
                {
                  term: "Name",
                  description: (
                    <span className="[overflow-wrap:anywhere]">{draft.name.trim() || "—"}</span>
                  ),
                },
                {
                  term: "Slug",
                  description: <span className="font-mono [overflow-wrap:anywhere]">{slug}</span>,
                },
                {
                  term: "Scope",
                  description: <span className="font-mono">{draft.scopeLevel}</span>,
                },
                { term: "Effect", description: <span className="font-mono">{draft.effect}</span> },
                {
                  term: "Action",
                  description: (
                    <span className="font-mono [overflow-wrap:anywhere]">
                      {draft.actionPattern.trim() || "*"}
                    </span>
                  ),
                },
                {
                  term: "Conditions",
                  description: <span className="font-mono tabular-nums">{conditionCount}</span>,
                },
              ]}
            />
            {submitError && (
              <p role="alert" className="text-destructive text-sm [overflow-wrap:anywhere]">
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
            <Button type="button" onClick={submit} disabled={creating}>
              {creating ? "Creating…" : "Create policy"}
            </Button>
          )}
        </div>
      </div>
    </div>
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
        <p className="text-destructive text-xs [overflow-wrap:anywhere]">{error}</p>
      ) : help ? (
        <p className="text-muted-foreground text-xs">{help}</p>
      ) : null}
    </div>
  );
}
