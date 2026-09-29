"use client";

import { AlertTriangleIcon, ArrowLeftIcon, ArrowRightIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
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
import { cn } from "@/lib/utils";

import { appsListCrumbs } from "./apps-area";
import { TRIGGER_KINDS, type TriggerKind } from "./deployments-format";
import type { StartField, useStartDeployment } from "./use-start-deployment";

export type StartDeploymentPageProps = ReturnType<typeof useStartDeployment> & {
  /** Start on a step; stories use it. */
  initialStep?: Step;
  /** Values and errors to open with; stories use them. */
  initialValues?: Partial<Values>;
  initialErrors?: Partial<Record<StartField, string>>;
};

type Step = 1 | 2 | 3;

interface Values {
  environmentName: string;
  imageTag: string;
  imageDigest: string;
  triggerKind: TriggerKind;
}

const STEPS: { n: Step; label: string }[] = [
  { n: 1, label: "Target" },
  { n: 2, label: "Image" },
  { n: 3, label: "Review" },
];

/** Which step holds a field, so a refused start opens where its error is. */
const STEP_OF: Record<StartField, Step> = {
  appSlug: 1,
  environmentName: 1,
  imageTag: 2,
  imageDigest: 2,
  triggerKind: 2,
};

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
  help?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-0 space-y-2">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {error && (
        <p
          id={`${id}-error`}
          role="alert"
          className="text-destructive text-xs [overflow-wrap:anywhere]"
        >
          {error}
        </p>
      )}
      {help && !error && (
        <p className="text-muted-foreground text-xs [overflow-wrap:anywhere]">{help}</p>
      )}
    </div>
  );
}

function invalid(id: string, error?: string) {
  return error ? { "aria-invalid": true, "aria-describedby": `${id}-error` } : {};
}

/**
 * Apps › Deployments › Start deployment: a page in three steps, since it
 * asks for five fields and deploys (spec 44 §5.4): the target, the image,
 * then a review. Errors stand beside their fields; the outcome is the
 * hook's toast. Pure view; the data half is useStartDeployment.
 */
export function StartDeploymentPage({
  apps,
  appsLoading,
  appsError,
  environments,
  environmentsLoading,
  appSlug,
  setAppSlug,
  submitting,
  onSubmit,
  cancelHref,
  initialStep = 1,
  initialValues = {},
  initialErrors = {},
}: StartDeploymentPageProps) {
  const t = useTranslations("lists.deployments.startSheet");
  const [step, setStep] = React.useState<Step>(initialStep);
  const [values, setValues] = React.useState<Values>({
    environmentName: "",
    imageTag: "",
    imageDigest: "",
    triggerKind: "manual",
    ...initialValues,
  });
  const [errors, setErrors] = React.useState<Partial<Record<StartField, string>>>(initialErrors);
  const [formError, setFormError] = React.useState<string | null>(null);

  const set = <K extends keyof Values>(key: K, value: Values[K]) => {
    setValues((v) => ({ ...v, [key]: value }));
    setErrors((e) => ({ ...e, [key]: undefined }));
  };

  const env = environments.find((e) => e.name === values.environmentName);
  const app = apps.find((a) => a.slug === appSlug);

  function check(at: Step): boolean {
    const found: Partial<Record<StartField, string>> = {};
    if (at === 1) {
      if (!appSlug) found.appSlug = "Pick the app to deploy.";
      if (!values.environmentName) found.environmentName = "Pick an environment.";
    }
    if (at === 2 && !values.imageTag.trim()) found.imageTag = "Enter the image tag to deploy.";
    setErrors((e) => ({ ...e, ...found }));
    return Object.keys(found).length === 0;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (step < 3) {
      if (check(step)) setStep((step + 1) as Step);
      return;
    }
    setFormError(null);
    const result = await onSubmit({ ...values, imageTag: values.imageTag.trim() });
    if (result.ok) return;
    setErrors(result.fieldErrors);
    setFormError(result.formError);
    const steps = (Object.keys(result.fieldErrors) as StartField[]).map((f) => STEP_OF[f]);
    if (steps.length > 0) setStep(Math.min(...steps) as Step);
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ShellHeader
        crumbs={appsListCrumbs("deployments", { label: t("title") })}
        title={t("title")}
        context={
          <ol aria-label="Steps" className="inline-flex flex-wrap items-center gap-2">
            {STEPS.map((s, i) => (
              <li
                key={s.n}
                aria-current={step === s.n ? "step" : undefined}
                className={cn(
                  "inline-flex items-center gap-1.5",
                  step === s.n ? "text-foreground font-medium" : "text-muted-foreground"
                )}
              >
                {i > 0 && (
                  <span aria-hidden className="text-muted-foreground">
                    ·
                  </span>
                )}
                <span className="font-mono">{s.n}</span> {s.label}
              </li>
            ))}
          </ol>
        }
      />

      <p className="text-muted-foreground max-w-2xl text-sm">{t("description")}</p>

      <form
        onSubmit={submit}
        noValidate
        className="bg-card flex max-w-3xl min-w-0 flex-col gap-4 rounded-md border p-6"
      >
        {(formError || appsError) && (
          <div
            role="alert"
            className="border-destructive/40 bg-destructive/5 text-destructive flex min-w-0 items-start gap-2 rounded-md border p-3 text-sm"
          >
            <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
            <span className="min-w-0 [overflow-wrap:anywhere]">{formError ?? appsError}</span>
          </div>
        )}

        {step === 1 && (
          <div className="grid min-w-0 gap-4 sm:grid-cols-2">
            <Field id="app" label={t("appLabel")} error={errors.appSlug}>
              <Select
                value={appSlug}
                onValueChange={(v) => {
                  setAppSlug(v);
                  set("environmentName", "");
                  setErrors((e) => ({ ...e, appSlug: undefined }));
                }}
                disabled={appsLoading}
              >
                <SelectTrigger
                  id="app"
                  className="w-full min-w-0"
                  {...invalid("app", errors.appSlug)}
                >
                  <SelectValue placeholder={appsLoading ? "Loading apps…" : t("appPlaceholder")} />
                </SelectTrigger>
                <SelectContent>
                  {apps.map((a) => (
                    <SelectItem key={a.id} value={a.slug}>
                      <span className="min-w-0 truncate">{a.name}</span>{" "}
                      <span className="text-muted-foreground font-mono text-xs">{a.slug}</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>

            <Field
              id="env"
              label={t("envLabel")}
              error={errors.environmentName}
              help={
                appSlug && !environmentsLoading && environments.length === 0
                  ? "This app has no environments yet. Bind it to a cluster first."
                  : undefined
              }
            >
              <Select
                value={values.environmentName}
                onValueChange={(v) => set("environmentName", v)}
                disabled={!appSlug || environmentsLoading}
              >
                <SelectTrigger
                  id="env"
                  className="w-full min-w-0"
                  {...invalid("env", errors.environmentName)}
                >
                  <SelectValue placeholder={t("envPlaceholder")} />
                </SelectTrigger>
                <SelectContent>
                  {environments.map((e) => (
                    <SelectItem key={e.id} value={e.name}>
                      <span className="font-mono">{e.name}</span>
                      {e.requiredApprovals > 0 && (
                        <span className="text-muted-foreground">
                          {" "}
                          · {t("approvalsSuffix", { count: e.requiredApprovals })}
                        </span>
                      )}
                      {e.deploysPaused && <span className="text-warning-fg"> · {t("paused")}</span>}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>
        )}

        {step === 2 && (
          <>
            <Field id="tag" label={t("tagLabel")} error={errors.imageTag}>
              <Input
                id="tag"
                value={values.imageTag}
                onChange={(e) => set("imageTag", e.target.value)}
                placeholder={t("tagPlaceholder")}
                className="font-mono text-xs"
                {...invalid("tag", errors.imageTag)}
              />
            </Field>
            <div className="grid min-w-0 gap-4 sm:grid-cols-2">
              <Field
                id="digest"
                label={t("digestLabel")}
                error={errors.imageDigest}
                help={t("digestHint")}
              >
                <Input
                  id="digest"
                  value={values.imageDigest}
                  onChange={(e) => set("imageDigest", e.target.value)}
                  placeholder={t("digestPlaceholder")}
                  className="font-mono text-xs"
                  {...invalid("digest", errors.imageDigest)}
                />
              </Field>
              <Field id="trigger" label={t("triggerLabel")} error={errors.triggerKind}>
                <Select
                  value={values.triggerKind}
                  onValueChange={(v) => set("triggerKind", v as TriggerKind)}
                >
                  <SelectTrigger
                    id="trigger"
                    className="w-full min-w-0 font-mono"
                    {...invalid("trigger", errors.triggerKind)}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TRIGGER_KINDS.map((k) => (
                      <SelectItem key={k} value={k} className="font-mono">
                        {k}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            </div>
          </>
        )}

        {step === 3 && (
          <div className="flex min-w-0 flex-col gap-3">
            <p className="text-sm">This starts a rollout. Check what is about to deploy.</p>
            <DefinitionList
              items={[
                {
                  term: t("appLabel"),
                  description: (
                    <span className="font-mono text-xs [overflow-wrap:anywhere]">
                      {app ? `${app.name} (${app.slug})` : appSlug}
                    </span>
                  ),
                },
                {
                  term: t("envLabel"),
                  description: (
                    <span className="font-mono text-xs [overflow-wrap:anywhere]">
                      {values.environmentName}
                    </span>
                  ),
                },
                {
                  term: t("tagLabel"),
                  description: (
                    <span className="font-mono text-xs [overflow-wrap:anywhere]">
                      {values.imageTag}
                    </span>
                  ),
                },
                {
                  term: t("digestLabel"),
                  description: (
                    <span className="font-mono text-xs [overflow-wrap:anywhere]">
                      {values.imageDigest.trim() || "—"}
                    </span>
                  ),
                },
                {
                  term: t("triggerLabel"),
                  description: <span className="font-mono text-xs">{values.triggerKind}</span>,
                },
              ]}
            />
            {env && env.requiredApprovals > 0 && (
              <p className="text-muted-foreground text-xs">
                {values.environmentName} needs{" "}
                <span className="font-mono">{env.requiredApprovals}</span> approval
                {env.requiredApprovals === 1 ? "" : "s"} before the rollout starts.
              </p>
            )}
            {env?.deploysPaused && (
              <p className="text-warning-fg text-xs">
                Deploys to {values.environmentName} are paused. A deployment started here still
                runs; CI and push triggers stay blocked.
              </p>
            )}
          </div>
        )}

        <div className="flex min-w-0 flex-wrap items-center justify-end gap-2 border-t pt-4">
          <Button type="button" variant="ghost" asChild className="mr-auto">
            <Link href={cancelHref}>{t("cancel")}</Link>
          </Button>
          {step > 1 && (
            <Button type="button" variant="outline" onClick={() => setStep((step - 1) as Step)}>
              <ArrowLeftIcon className="size-4" />
              Back
            </Button>
          )}
          {step < 3 ? (
            <Button type="submit">
              Continue
              <ArrowRightIcon className="size-4" />
            </Button>
          ) : (
            <Button type="submit" disabled={submitting}>
              {submitting ? t("submitting") : t("submit")}
            </Button>
          )}
        </div>
      </form>
    </div>
  );
}
