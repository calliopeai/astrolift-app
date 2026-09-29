"use client";

import * as React from "react";

import { SCOPE_NOUN, SCOPE_ORDER } from "@/components/access/access-model";
import { PermissionMatrix } from "@/components/access/PermissionMatrix";
import { RoleSummary } from "@/components/access/RoleSummary";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";
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
import type { AstroliftRole, ScopeKind } from "@/graphql/identity/identity.types";
import { cn } from "@/lib/utils";

import { permissionsCrumbs } from "../access/admin-crumbs";
import { ROLES_HREF } from "./roles-routes";

/** What the create page submits; the hook trims and normalises it. */
export interface RoleDraft {
  slug: string;
  name: string;
  description: string;
  scopeLevel: ScopeKind;
  /** Sorted permission slugs. */
  permissions: string[];
}

type Step = 0 | 1 | 2;
const STEPS = ["Details", "Permissions", "Review"] as const;
const BLANK = "blank";

export interface NewRoleScreenProps {
  /** Every role, to start from. */
  roles: AstroliftRole[];
  /** The role being duplicated (`?from=`), once the roles have loaded. */
  from: AstroliftRole | null;
  /** The whole catalog (roleCatalog). */
  catalog: string[];
  loading: boolean;
  creating: boolean;
  /** Null when created (the hook moves on), or the server's reason, shown on the review. */
  onCreate: (draft: RoleDraft) => Promise<string | null>;
  onCancel: () => void;
  /** Stories: open on this step. */
  initialStep?: Step;
  /** Stories: seed the draft. */
  initialDraft?: Partial<RoleDraft>;
}

const slugify = (s: string) =>
  s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);

function seed(from: AstroliftRole | null): RoleDraft {
  return {
    slug: "",
    name: from ? `${from.name} (custom)` : "",
    description: from?.description ?? "",
    scopeLevel: from?.scopeLevel ?? "ORG",
    permissions: [...(from?.permissions ?? [])].sort(),
  };
}

/**
 * New role (design 3.5, spec 44 §5.4): five fields, so a page with numbered
 * steps. Details, then the permissions as an editable `PermissionMatrix`
 * diffed against the role it starts from, then a review of what it will
 * allow before anything is written. Duplicating a built-in (`?from=`) starts
 * from its permissions; the caller keys it by `from`, so it reseeds when
 * the roles land. Errors sit beside their field. Pure.
 */
export function NewRoleScreen({
  roles,
  from,
  catalog,
  loading,
  creating,
  onCreate,
  onCancel,
  initialStep = 0,
  initialDraft,
}: NewRoleScreenProps) {
  const [step, setStep] = React.useState<Step>(initialStep);
  const [origin, setOrigin] = React.useState<AstroliftRole | null>(from);
  const [draft, setDraft] = React.useState<RoleDraft>({ ...seed(from), ...initialDraft });
  const [slugTouched, setSlugTouched] = React.useState(Boolean(initialDraft?.slug));
  const [errors, setErrors] = React.useState<Partial<Record<"name" | "slug", string>>>({});
  const [submitError, setSubmitError] = React.useState<string | null>(null);

  const patch = (next: Partial<RoleDraft>) => setDraft((d) => ({ ...d, ...next }));
  const slug = slugTouched ? draft.slug : slugify(draft.name);
  const full = { ...draft, slug };

  function startFrom(id: string) {
    const r = roles.find((x) => x.id === id) ?? null;
    setOrigin(r);
    patch({
      permissions: [...(r?.permissions ?? [])].sort(),
      scopeLevel: r?.scopeLevel ?? draft.scopeLevel,
    });
  }

  function validate(): boolean {
    const next: typeof errors = {};
    if (!draft.name.trim()) next.name = "Name the role.";
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(slug))
      next.slug =
        "Lowercase letters, digits, hyphens and underscores, starting with a letter or digit.";
    else if (roles.some((r) => r.slug === slug))
      next.slug = `A role called ${slug} already exists.`;
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  function goTo(target: Step) {
    if (target > 0 && step === 0 && !validate()) return;
    setStep(target);
  }

  async function submit() {
    if (!validate()) {
      setStep(0);
      return;
    }
    setSubmitError(await onCreate(full));
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
      <ShellHeader
        crumbs={[
          ...permissionsCrumbs("roles").slice(0, 2),
          { label: "Roles", href: ROLES_HREF },
          { label: from ? `Duplicate ${from.name}` : "New role" },
        ]}
        title={from ? `Duplicate ${from.name}` : "New role"}
        context="A custom role is a named set of permissions you grant at a scope."
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
            <Field id="role-from" label="Start from">
              <Select value={origin?.id ?? BLANK} onValueChange={startFrom} disabled={loading}>
                <SelectTrigger id="role-from" className="max-w-md min-w-0">
                  <SelectValue placeholder={loading ? "Loading roles…" : undefined} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={BLANK}>Nothing, a blank role</SelectItem>
                  {roles.map((r) => (
                    <SelectItem key={r.id} value={r.id}>
                      {r.name}
                      {r.isSystem ? " (built-in)" : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <div className="grid min-w-0 gap-4 sm:grid-cols-2">
              <Field id="role-name" label="Name" error={errors.name}>
                <Input
                  id="role-name"
                  value={draft.name}
                  onChange={(e) => patch({ name: e.target.value })}
                  placeholder="Release manager"
                  aria-invalid={Boolean(errors.name)}
                />
              </Field>
              <Field
                id="role-slug"
                label="Slug"
                error={errors.slug}
                help="Derived from the name until you edit it. Fixed once created."
              >
                <Input
                  id="role-slug"
                  value={slug}
                  onChange={(e) => {
                    setSlugTouched(true);
                    patch({ slug: e.target.value });
                  }}
                  className="font-mono"
                  aria-invalid={Boolean(errors.slug)}
                />
              </Field>
            </div>
            <Field
              id="role-scope"
              label="Binds at"
              help="Where it may be granted: at this level or anything inside it. Fixed once created."
            >
              <Select
                value={draft.scopeLevel}
                onValueChange={(v) => patch({ scopeLevel: v as ScopeKind })}
              >
                <SelectTrigger id="role-scope" className="max-w-xs min-w-0">
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
            <Field id="role-description" label="Description">
              <Textarea
                id="role-description"
                value={draft.description}
                onChange={(e) => patch({ description: e.target.value })}
                placeholder="What this role is for, in one sentence."
                rows={2}
              />
            </Field>
          </>
        )}

        {step === 1 && (
          <PermissionMatrix
            permissions={draft.permissions}
            catalog={catalog}
            onChange={(permissions) => patch({ permissions })}
            base={origin?.permissions}
            baseLabel={origin?.name}
          />
        )}

        {step === 2 && (
          <>
            <RoleSummary
              role={{ ...full, id: "draft", isSystem: false }}
              catalog={catalog}
              base={origin}
              defaultOpen
            />
            {origin && (
              <p className="text-muted-foreground text-xs">
                Compared with {origin.name}. The role will not remember it came from there: the
                backend does not record lineage yet.
              </p>
            )}
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
            <Button type="button" onClick={submit} disabled={creating}>
              {creating ? "Creating…" : "Create role"}
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
        <p className="text-danger text-xs [overflow-wrap:anywhere]">{error}</p>
      ) : help ? (
        <p className="text-muted-foreground text-xs">{help}</p>
      ) : null}
    </div>
  );
}
