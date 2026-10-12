"use client";

import Link from "next/link";
import * as React from "react";
import { useTranslations } from "next-intl";
import { AlertTriangleIcon, ArrowLeftIcon, ArrowRightIcon, Loader2Icon } from "lucide-react";

import { FlowSteps } from "@/components/screens/agents/skills/FlowSteps";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { WorkflowView } from "@/components/viz/WorkflowView";
import { definitionLine, definitionSnapshot } from "@/components/workflows/definition-line";
import { workflowsCrumbs } from "@/components/screens/workflows/list/workflows-list";
import type { WorkflowManifestPreview } from "@/graphql/workflows/tiered.types";
import { cn } from "@/lib/utils";

import type { useWorkflowBuilder } from "./use-workflow-builder";
import {
  manifestLineStages,
  type NewWorkflowErrors,
  type NewWorkflowSource,
  PATTERNS,
  slugify,
  type PatternKind,
  type PatternOption,
  validateSource,
} from "./workflow-patterns";

export type WorkflowBuilderScreenProps = ReturnType<typeof useWorkflowBuilder> & {
  /** Pattern preselected from `?pattern=`; read once on mount. */
  initialPattern: PatternKind;
  /** Start on a step or source; stories use them. */
  initialStep?: Step;
  initialSource?: NewWorkflowSource;
  initialPreview?: WorkflowManifestPreview | null;
  initialErrors?: NewWorkflowErrors;
};

type Step = 1 | 2 | 3;

const STEPS = ["source", "stages", "review"] as const;

export function PatternCard({
  pattern,
  selected,
  onSelect,
}: {
  pattern: PatternOption;
  selected: boolean;
  onSelect: () => void;
}) {
  const t = useTranslations("workflowNew");
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        "flex min-w-0 flex-col gap-2 rounded-md border p-4 text-left transition-colors",
        selected ? "border-primary bg-primary/5" : "hover:bg-muted border-border"
      )}
    >
      <div className="flex min-w-0 items-center justify-between gap-2">
        <span className="min-w-0 truncate font-medium">{t(`patterns.${pattern.value}.label`)}</span>
        {selected && (
          <Badge variant="default" className="shrink-0 text-xs">
            {t("selected")}
          </Badge>
        )}
      </div>
      <p className="text-muted-foreground text-xs">{t(`patterns.${pattern.value}.description`)}</p>
      <pre className="text-muted-foreground mt-1 overflow-x-auto font-mono text-xs leading-relaxed">
        {pattern.diagram}
      </pre>
    </button>
  );
}

function SourceChoice({
  value,
  onChange,
}: {
  value: NewWorkflowSource;
  onChange: (source: NewWorkflowSource) => void;
}) {
  const t = useTranslations("workflowNew");
  const options: NewWorkflowSource[] = ["pattern", "manifest"];
  return (
    <fieldset className="grid min-w-0 gap-3 sm:grid-cols-2">
      <legend className="mb-2 text-sm font-medium">{t("startFrom")}</legend>
      {options.map((o) => (
        <label
          key={o}
          className={cn(
            "flex min-w-0 cursor-pointer items-start gap-3 rounded-md border p-3",
            value === o ? "border-primary bg-primary/5" : "hover:bg-muted"
          )}
        >
          <input
            type="radio"
            name="new-workflow-source"
            value={o}
            checked={value === o}
            onChange={() => onChange(o)}
            className="accent-primary mt-1"
          />
          <span className="min-w-0">
            <span className="block text-sm font-medium">{t(`sources.${o}.label`)}</span>
            <span className="text-muted-foreground block text-xs">{t(`sources.${o}.hint`)}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

function ReviewRow({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="grid min-w-0 gap-1 sm:grid-cols-[10rem_minmax(0,1fr)]">
      <dt className="text-muted-foreground text-sm">{term}</dt>
      <dd className="min-w-0 text-sm [overflow-wrap:anywhere]">{children}</dd>
    </div>
  );
}

/**
 * Agents › Workflows › New workflow, a page in three steps (spec 44 §5.4):
 * Source (a pattern with its name, or a manifest), Stages (the manifest's
 * stages drawn as the person's workflow view, or the pattern's shape to fill
 * in the Builder), Review, then Create. Errors stand beside their fields; a
 * refused name or manifest sends the page back to Source; the outcome is the
 * hook's toast. A platform template is configured from the Templates view
 * instead. Holds the field values; useWorkflowBuilder creates the workflow.
 */
export function WorkflowBuilderScreen({
  canCreate,
  entitlementLoading,
  creating,
  previewing,
  onCreate,
  onImport,
  onPreview,
  initialPattern,
  initialStep = 1,
  initialSource = "pattern",
  initialPreview = null,
  initialErrors = {},
}: WorkflowBuilderScreenProps) {
  const t = useTranslations("workflowNew");
  const [step, setStep] = React.useState<Step>(initialStep);
  const [source, setSource] = React.useState<NewWorkflowSource>(initialSource);
  const [name, setName] = React.useState("");
  const [slug, setSlug] = React.useState("");
  const [slugTouched, setSlugTouched] = React.useState(false);
  const [description, setDescription] = React.useState("");
  const [pattern, setPattern] = React.useState<PatternKind>(initialPattern);
  const [toml, setToml] = React.useState("");
  const [preview, setPreview] = React.useState<WorkflowManifestPreview | null>(initialPreview);
  const [errors, setErrors] = React.useState<NewWorkflowErrors>(initialErrors);

  const chosen = PATTERNS.find((p) => p.value === pattern) ?? PATTERNS[0];
  const finalSlug = slug.trim() || slugify(name);
  const clear = (field: keyof NewWorkflowErrors) =>
    setErrors((e) => ({ ...e, [field]: undefined, form: undefined }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (step === 3 && (creating || entitlementLoading || !canCreate)) return;
    if (step === 1) {
      const found = validateSource(source, { name, slug, toml });
      setErrors(found);
      if (Object.values(found).some(Boolean)) return;
      if (source === "manifest") {
        const result = await onPreview(toml);
        if (!result.preview) return setErrors({ toml: result.error });
        setPreview(result.preview);
      }
      return setStep(2);
    }
    if (step === 2) return setStep(3);
    const found =
      source === "manifest"
        ? await onImport(toml)
        : await onCreate({ name, slug, description, pattern });
    setErrors(found);
    if (found.name || found.slug || found.toml) setStep(1);
  }

  const manifestStages = preview ? manifestLineStages(preview.stages) : [];

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ShellHeader
        crumbs={workflowsCrumbs({ label: t("title") })}
        title={t("title")}
        context={
          <FlowSteps steps={STEPS.map((s) => ({ label: t(`steps.${s}`) }))} current={step} />
        }
      />

      <p className="text-muted-foreground max-w-2xl text-sm">
        {t.rich("intro", {
          link: (chunks) => (
            <Link href="/workflows?view=templates" className="text-foreground underline">
              {chunks}
            </Link>
          ),
        })}
      </p>

      <form
        onSubmit={submit}
        noValidate
        className="bg-card flex max-w-4xl min-w-0 flex-col gap-5 rounded-md border p-6"
      >
        {errors.form && (
          <div
            role="alert"
            className="border-destructive/40 bg-destructive/5 text-destructive flex min-w-0 items-start gap-2 rounded-md border p-3 text-sm"
          >
            <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span className="min-w-0 [overflow-wrap:anywhere]">{errors.form}</span>
          </div>
        )}

        {step === 1 && (
          <>
            <SourceChoice
              value={source}
              onChange={(next) => {
                setSource(next);
                setErrors({});
              }}
            />
            {source === "pattern" ? (
              <>
                <div className="grid min-w-0 gap-4 sm:grid-cols-2">
                  <Field data-invalid={Boolean(errors.name) || undefined} className="min-w-0">
                    <FieldLabel htmlFor="new-workflow-name">{t("name")}</FieldLabel>
                    <Input
                      id="new-workflow-name"
                      value={name}
                      placeholder={t("example", { value: "PR Review Loop" })}
                      aria-invalid={Boolean(errors.name) || undefined}
                      onChange={(e) => {
                        setName(e.target.value);
                        clear("name");
                        if (!slugTouched) setSlug(slugify(e.target.value));
                      }}
                    />
                    <FieldError className="[overflow-wrap:anywhere]">{errors.name}</FieldError>
                  </Field>
                  <Field data-invalid={Boolean(errors.slug) || undefined} className="min-w-0">
                    <FieldLabel htmlFor="new-workflow-slug">{t("slug")}</FieldLabel>
                    <Input
                      id="new-workflow-slug"
                      value={slug}
                      placeholder={t("example", { value: "pr-review-loop" })}
                      className="font-mono"
                      spellCheck={false}
                      aria-invalid={Boolean(errors.slug) || undefined}
                      onChange={(e) => {
                        setSlugTouched(true);
                        setSlug(slugify(e.target.value));
                        clear("slug");
                      }}
                    />
                    <FieldError className="[overflow-wrap:anywhere]">{errors.slug}</FieldError>
                  </Field>
                </div>
                <Field className="min-w-0">
                  <FieldLabel htmlFor="new-workflow-description">{t("description")}</FieldLabel>
                  <Input
                    id="new-workflow-description"
                    value={description}
                    placeholder={t("descriptionPlaceholder")}
                    onChange={(e) => setDescription(e.target.value)}
                  />
                </Field>
                <fieldset className="flex min-w-0 flex-col gap-2">
                  <legend className="mb-2 text-sm font-medium">{t("pattern")}</legend>
                  <div className="grid min-w-0 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {PATTERNS.map((p) => (
                      <PatternCard
                        key={p.value}
                        pattern={p}
                        selected={pattern === p.value}
                        onSelect={() => setPattern(p.value)}
                      />
                    ))}
                  </div>
                </fieldset>
              </>
            ) : (
              <Field data-invalid={Boolean(errors.toml) || undefined} className="min-w-0">
                <FieldLabel htmlFor="new-workflow-toml">{t("manifest")}</FieldLabel>
                <Textarea
                  id="new-workflow-toml"
                  value={toml}
                  rows={12}
                  spellCheck={false}
                  placeholder={
                    '[workflow]\nslug = "pr-review-loop"\nname = "PR Review Loop"\n\n[[stages]]\nkind = "agent_dispatch"'
                  }
                  className="max-h-96 font-mono text-xs"
                  aria-invalid={Boolean(errors.toml) || undefined}
                  onChange={(e) => {
                    setToml(e.target.value);
                    setPreview(null);
                    clear("toml");
                  }}
                />
                <FieldDescription>{t("manifestHint")}</FieldDescription>
                <FieldError className="[overflow-wrap:anywhere]">{errors.toml}</FieldError>
              </Field>
            )}
          </>
        )}

        {step === 2 &&
          (source === "manifest" && preview ? (
            manifestStages.length > 0 ? (
              <WorkflowView
                snapshot={definitionSnapshot(
                  definitionLine(
                    {
                      slug: preview.definition?.slug ?? "manifest",
                      name: preview.definition?.name ?? "Manifest",
                      patternKind: preview.definition?.pattern ?? "",
                    },
                    manifestStages
                  )
                )}
                title={t("stages")}
                description={t("stagesFromManifest", { count: manifestStages.length })}
              />
            ) : (
              <p className="text-muted-foreground text-sm">{t("manifestNoStages")}</p>
            )
          ) : (
            <div className="flex min-w-0 flex-col gap-3">
              <p className="text-sm">
                {t.rich("patternNoStages", {
                  pattern: t(`patterns.${chosen.value}.label`),
                  strong: (chunks) => <span className="font-medium">{chunks}</span>,
                })}
              </p>
              <pre className="bg-muted/40 text-muted-foreground overflow-x-auto rounded-md border p-3 font-mono text-xs leading-relaxed">
                {chosen.diagram}
              </pre>
            </div>
          ))}

        {step === 3 && (
          <dl className="flex min-w-0 flex-col gap-3">
            {source === "manifest" ? (
              <>
                <ReviewRow term={t("name")}>
                  {preview?.definition?.name ?? t("fromManifest")}
                </ReviewRow>
                <ReviewRow term={t("slug")}>
                  <span className="font-mono">
                    {preview?.definition?.slug ?? t("fromManifest")}
                  </span>
                </ReviewRow>
                <ReviewRow term={t("pattern")}>
                  {preview?.definition?.pattern || t("none")}
                </ReviewRow>
                <ReviewRow term={t("stages")}>
                  <span className="font-mono">{preview?.stages.length ?? 0}</span>
                </ReviewRow>
              </>
            ) : (
              <>
                <ReviewRow term={t("name")}>{name}</ReviewRow>
                <ReviewRow term={t("slug")}>
                  <span className="font-mono">{finalSlug}</span>
                </ReviewRow>
                <ReviewRow term={t("description")}>{description || t("none")}</ReviewRow>
                <ReviewRow term={t("pattern")}>{t(`patterns.${chosen.value}.label`)}</ReviewRow>
                <ReviewRow term={t("stages")}>{t("addedInBuilder")}</ReviewRow>
              </>
            )}
          </dl>
        )}

        <div className="flex min-w-0 flex-wrap items-center justify-end gap-2 border-t pt-4">
          <Button type="button" variant="ghost" asChild className="mr-auto">
            <Link href="/workflows">{t("cancel")}</Link>
          </Button>
          {step > 1 && (
            <Button type="button" variant="outline" onClick={() => setStep((s) => (s - 1) as Step)}>
              <ArrowLeftIcon className="size-4" />
              {t("back")}
            </Button>
          )}
          {step < 3 ? (
            <Button type="submit" disabled={previewing}>
              {previewing && <Loader2Icon className="size-4 animate-spin" />}
              {t("continue")}
              <ArrowRightIcon className="size-4" />
            </Button>
          ) : (
            <Button
              type="submit"
              disabled={creating || entitlementLoading || !canCreate}
              title={entitlementLoading || canCreate ? undefined : t("noCreateAccess")}
            >
              {(creating || entitlementLoading) && <Loader2Icon className="size-4 animate-spin" />}
              {t("create")}
            </Button>
          )}
        </div>
      </form>
    </div>
  );
}
