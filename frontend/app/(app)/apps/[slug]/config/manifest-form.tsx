"use client";

/**
 * Schema-driven visual editor for astrolift.toml (#1110).
 *
 * Renders the manifest model as form sections generated from the schema
 * descriptor, with list→detail drill-down sub-editors for workloads /
 * containers / managed services (the #1106 principle). The parent owns the
 * TOML round-trip; this component is a pure controlled view over
 * `ManifestModel` — every edit bubbles a new model up via `onChange`.
 */

import {
  BoxIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ContainerIcon,
  DatabaseIcon,
  PlusIcon,
  TrashIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { EmptyState } from "@/components/EmptyState";
import { Section } from "@/components/ui/section";
import { Separator } from "@/components/ui/separator";
import {
  type ContainerModel,
  type ManagedServiceModel,
  type ManifestModel,
  type WorkloadModel,
  emptyContainer,
  emptyService,
  emptyWorkload,
} from "@/lib/manifest/model";
import {
  CONTAINER_FIELDS,
  HEALTHCHECK_KINDS,
  MANAGED_SERVICE_KINDS,
  WORKLOAD_KIND_OPTIONS,
  WORKLOAD_RESOURCE_FIELDS,
  type ManifestErr,
  errorFor,
  hasErrorPrefix,
  supportsContainers,
  supportsResources,
  tuningFields,
} from "@/lib/manifest/schema";
import type { TomlValue } from "@/lib/manifest/toml";

import {
  KeyValueEditor,
  ListField,
  NumberField,
  SelectField,
  SpecField,
  TextField,
  ToggleField,
} from "./manifest-fields";

type Props = {
  model: ManifestModel;
  errors: ManifestErr[];
  onChange: (model: ManifestModel) => void;
};

/** Patch a workload's per-kind extra bag, dropping keys set to undefined. */
function patchBag(bag: Record<string, TomlValue>, key: string, value: TomlValue | undefined) {
  const next = { ...bag };
  if (value === undefined) delete next[key];
  else next[key] = value;
  return next;
}

function CollapsibleEntry({
  open,
  onOpenChange,
  icon,
  title,
  subtitle,
  badges,
  invalid,
  onRemove,
  removeLabel,
  children,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  badges?: React.ReactNode;
  invalid?: boolean;
  onRemove: () => void;
  removeLabel: string;
  children: React.ReactNode;
}) {
  return (
    <Card className={invalid ? "border-destructive/40" : undefined}>
      <Collapsible open={open} onOpenChange={onOpenChange}>
        <CardContent className="flex flex-col gap-3 p-4">
          <div className="flex items-center gap-2">
            <CollapsibleTrigger asChild>
              <button
                type="button"
                className="text-muted-foreground hover:text-foreground rounded p-0.5"
                aria-label={open ? "Collapse" : "Expand"}
              >
                {open ? (
                  <ChevronDownIcon className="size-4" />
                ) : (
                  <ChevronRightIcon className="size-4" />
                )}
              </button>
            </CollapsibleTrigger>
            <span className="text-muted-foreground shrink-0">{icon}</span>
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <span className="truncate font-mono text-sm font-medium">
                {title || <span className="text-muted-foreground italic">unnamed</span>}
              </span>
              {subtitle && (
                <span className="text-muted-foreground truncate text-xs">{subtitle}</span>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-1">{badges}</div>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="text-muted-foreground hover:text-destructive size-7 shrink-0"
              onClick={onRemove}
              title={removeLabel}
              aria-label={removeLabel}
            >
              <TrashIcon className="size-3.5" />
            </Button>
          </div>
          <CollapsibleContent className="flex flex-col gap-4">
            <Separator />
            {children}
          </CollapsibleContent>
        </CardContent>
      </Collapsible>
    </Card>
  );
}

// ─── Container sub-editor ────────────────────────────────────────────────────

function ContainerEditor({
  container,
  path,
  errors,
  onChange,
  onRemove,
}: {
  container: ContainerModel;
  path: string;
  errors: ManifestErr[];
  onChange: (c: ContainerModel) => void;
  onRemove: () => void;
}) {
  const t = useTranslations("apps.config.builder");
  const [open, setOpen] = React.useState(true);
  const patch = (p: Partial<ContainerModel>) => onChange({ ...container, ...p });
  const hc = container.healthcheck;

  return (
    <CollapsibleEntry
      open={open}
      onOpenChange={setOpen}
      icon={<ContainerIcon className="size-4" />}
      title={container.name}
      subtitle={container.image_ref}
      invalid={hasErrorPrefix(errors, path)}
      onRemove={onRemove}
      removeLabel={t("removeContainer")}
      badges={
        container.is_primary ? (
          <Badge variant="secondary" className="text-2xs">
            {t("primary")}
          </Badge>
        ) : undefined
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label={t("containerName")}
          value={container.name}
          mono
          error={errorFor(errors, `${path}.name`)}
          onChange={(v) => patch({ name: v })}
        />
        <ToggleField
          label={t("isPrimary")}
          value={container.is_primary}
          onChange={(v) => patch({ is_primary: v })}
        />
        {CONTAINER_FIELDS.map((spec) =>
          spec.widget === "number" ? (
            <NumberField
              key={spec.key}
              label={spec.label}
              placeholder={spec.placeholder}
              value={(container[spec.key as keyof ContainerModel] as number | null) ?? null}
              onChange={(v) => patch({ [spec.key]: v } as Partial<ContainerModel>)}
            />
          ) : (
            <TextField
              key={spec.key}
              label={spec.label}
              mono
              placeholder={spec.placeholder}
              value={(container[spec.key as keyof ContainerModel] as string) ?? ""}
              onChange={(v) => patch({ [spec.key]: v } as Partial<ContainerModel>)}
            />
          )
        )}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <ListField
          label={t("command")}
          value={container.command}
          placeholder={t("commandHint")}
          onChange={(v) => patch({ command: v })}
        />
        <ListField
          label={t("args")}
          value={container.args}
          placeholder={t("argsHint")}
          onChange={(v) => patch({ args: v })}
        />
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-xs font-medium">{t("containerEnv")}</span>
        <KeyValueEditor
          entries={container.env}
          onChange={(env) => patch({ env })}
          addLabel={t("addEnv")}
        />
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-xs font-medium">{t("healthcheck")}</span>
        <div className="grid gap-3 sm:grid-cols-3">
          <SelectField
            label={t("hcKind")}
            value={hc.kind}
            options={HEALTHCHECK_KINDS}
            error={errorFor(errors, `${path}.healthcheck`)}
            onChange={(v) => patch({ healthcheck: { ...hc, kind: v } })}
          />
          {hc.kind !== "none" && (
            <>
              <TextField
                label={t("hcValue")}
                value={hc.value}
                mono
                placeholder="/health"
                onChange={(v) => patch({ healthcheck: { ...hc, value: v } })}
              />
              <NumberField
                label={t("hcPort")}
                value={hc.port}
                onChange={(v) => patch({ healthcheck: { ...hc, port: v } })}
              />
            </>
          )}
        </div>
      </div>
    </CollapsibleEntry>
  );
}

// ─── Workload sub-editor ─────────────────────────────────────────────────────

function WorkloadEditor({
  workload,
  index,
  errors,
  onChange,
  onRemove,
}: {
  workload: WorkloadModel;
  index: number;
  errors: ManifestErr[];
  onChange: (w: WorkloadModel) => void;
  onRemove: () => void;
}) {
  const t = useTranslations("apps.config.builder");
  const [open, setOpen] = React.useState(true);
  const path = `workloads[${index}]`;
  const patch = (p: Partial<WorkloadModel>) => onChange({ ...workload, ...p });
  const patchExtra = (key: string, value: TomlValue | undefined) =>
    onChange({ ...workload, extra: patchBag(workload.extra, key, value) });

  const kindTuning = tuningFields(workload.kind);

  return (
    <CollapsibleEntry
      open={open}
      onOpenChange={setOpen}
      icon={<BoxIcon className="size-4" />}
      title={workload.name}
      invalid={hasErrorPrefix(errors, path)}
      onRemove={onRemove}
      removeLabel={t("removeWorkload")}
      badges={
        <>
          <Badge variant="outline" className="text-2xs">
            {workload.kind}
          </Badge>
          {workload.is_public && (
            <Badge variant="secondary" className="text-2xs">
              {t("public")}
            </Badge>
          )}
          {supportsContainers(workload.kind) && (
            <Badge variant="outline" className="text-2xs">
              {t("containerCount", { count: workload.containers.length })}
            </Badge>
          )}
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label={t("workloadName")}
          value={workload.name}
          mono
          error={errorFor(errors, `${path}.name`)}
          onChange={(v) => patch({ name: v })}
        />
        <SelectField
          label={t("kind")}
          value={workload.kind}
          options={WORKLOAD_KIND_OPTIONS}
          error={errorFor(errors, `${path}.kind`)}
          onChange={(v) => patch({ kind: v })}
        />
        <ToggleField
          label={t("isPublic")}
          value={workload.is_public}
          onChange={(v) => patch({ is_public: v })}
        />
        <NumberField
          label={t("replicas")}
          value={workload.replicas}
          placeholder="1"
          onChange={(v) => patch({ replicas: v })}
        />
      </div>

      {workload.kind === "cronjob" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField
            label={t("schedule")}
            value={workload.schedule}
            mono
            placeholder="0 * * * *"
            error={errorFor(errors, `${path}.schedule`)}
            onChange={(v) => patch({ schedule: v })}
          />
          <SelectField
            label={t("concurrencyPolicy")}
            value={workload.concurrency_policy || "forbid"}
            options={["forbid", "queue", "replace"]}
            onChange={(v) => patch({ concurrency_policy: v })}
          />
        </div>
      )}

      {supportsResources(workload.kind) && (
        <div className="flex flex-col gap-2">
          <span className="text-xs font-medium">{t("resources")}</span>
          <div className="grid gap-3 sm:grid-cols-3">
            {WORKLOAD_RESOURCE_FIELDS.map((spec) =>
              spec.widget === "number" ? (
                <NumberField
                  key={spec.key}
                  label={spec.label}
                  placeholder={spec.placeholder}
                  value={(workload[spec.key as keyof WorkloadModel] as number | null) ?? null}
                  onChange={(v) => patch({ [spec.key]: v } as Partial<WorkloadModel>)}
                />
              ) : (
                <TextField
                  key={spec.key}
                  label={spec.label}
                  mono
                  placeholder={spec.placeholder}
                  value={(workload[spec.key as keyof WorkloadModel] as string) ?? ""}
                  onChange={(v) => patch({ [spec.key]: v } as Partial<WorkloadModel>)}
                />
              )
            )}
          </div>
        </div>
      )}

      {kindTuning.length > 0 && (
        <div className="flex flex-col gap-2">
          <span className="text-xs font-medium">{t("tuning", { kind: workload.kind })}</span>
          <div className="grid gap-3 sm:grid-cols-3">
            {kindTuning.map((spec) => (
              <SpecField
                key={spec.key}
                spec={spec}
                bag={workload.extra}
                error={errorFor(errors, `${path}.${spec.key}`)}
                onPatch={patchExtra}
              />
            ))}
          </div>
        </div>
      )}

      {supportsContainers(workload.kind) && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium">{t("containers")}</span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7"
              onClick={() =>
                patch({ containers: [...workload.containers, emptyContainer(`c${workload.containers.length + 1}`)] })
              }
            >
              <PlusIcon className="size-3" /> {t("addContainer")}
            </Button>
          </div>
          {errorFor(errors, `${path}.containers`) && (
            <p className="text-destructive text-xs">{errorFor(errors, `${path}.containers`)}</p>
          )}
          {workload.containers.length === 0 ? (
            <p className="text-muted-foreground text-xs">{t("noContainers")}</p>
          ) : (
            workload.containers.map((c, ci) => (
              <ContainerEditor
                key={ci}
                container={c}
                path={`${path}.containers[${ci}]`}
                errors={errors}
                onChange={(next) =>
                  patch({ containers: workload.containers.map((x, j) => (j === ci ? next : x)) })
                }
                onRemove={() => patch({ containers: workload.containers.filter((_, j) => j !== ci) })}
              />
            ))
          )}
        </div>
      )}
    </CollapsibleEntry>
  );
}

// ─── Managed service sub-editor ──────────────────────────────────────────────

function ServiceEditor({
  service,
  index,
  errors,
  onChange,
  onRemove,
}: {
  service: ManagedServiceModel;
  index: number;
  errors: ManifestErr[];
  onChange: (s: ManagedServiceModel) => void;
  onRemove: () => void;
}) {
  const t = useTranslations("apps.config.builder");
  const [open, setOpen] = React.useState(true);
  const path = `managedServices[${index}]`;
  const patch = (p: Partial<ManagedServiceModel>) => onChange({ ...service, ...p });

  return (
    <CollapsibleEntry
      open={open}
      onOpenChange={setOpen}
      icon={<DatabaseIcon className="size-4" />}
      title={service.name || service.kind}
      subtitle={service.name ? service.kind : undefined}
      invalid={hasErrorPrefix(errors, path)}
      onRemove={onRemove}
      removeLabel={t("removeService")}
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <TextField
          label={t("serviceKind")}
          value={service.kind}
          mono
          help={MANAGED_SERVICE_KINDS.join(", ")}
          error={errorFor(errors, `${path}.kind`)}
          onChange={(v) => patch({ kind: v })}
        />
        <TextField
          label={t("serviceName")}
          value={service.name}
          mono
          onChange={(v) => patch({ name: v })}
        />
        <TextField
          label={t("serviceVariant")}
          value={service.variant}
          onChange={(v) => patch({ variant: v })}
        />
      </div>
      <div className="flex flex-col gap-2">
        <span className="text-xs font-medium">{t("serviceConfig")}</span>
        <KeyValueEditor
          entries={service.config}
          onChange={(config) => patch({ config })}
          addLabel={t("addConfig")}
        />
      </div>
    </CollapsibleEntry>
  );
}

// ─── Root form ───────────────────────────────────────────────────────────────

export function ManifestFormBuilder({ model, errors, onChange }: Props) {
  const t = useTranslations("apps.config.builder");
  const patch = (p: Partial<ManifestModel>) => onChange({ ...model, ...p });
  const preservedKeys = Object.keys(model.extra);

  return (
    <div className="flex flex-col gap-6">
      <Section title={t("appSection")} description={t("appHint")}>
        <div className="max-w-md">
          <TextField
            label={t("appName")}
            value={model.name}
            mono
            error={errorFor(errors, "name")}
            onChange={(v) => patch({ name: v })}
          />
        </div>
      </Section>

      <Section title={t("environment")} description={t("envHint")}>
        <KeyValueEditor
          entries={model.env}
          onChange={(env) => patch({ env })}
          addLabel={t("addEnv")}
        />
      </Section>

      <Section
        title={t("workloads")}
        description={t("workloadsHint")}
        action={
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => patch({ workloads: [...model.workloads, emptyWorkload(`workload-${model.workloads.length + 1}`)] })}
          >
            <PlusIcon className="size-3.5" /> {t("addWorkload")}
          </Button>
        }
      >
        {model.workloads.length === 0 ? (
          <EmptyState
            icon={<BoxIcon className="size-5" />}
            title={t("noWorkloads")}
            description={t("noWorkloadsHint")}
          />
        ) : (
          <div className="flex flex-col gap-3">
            {model.workloads.map((w, i) => (
              <WorkloadEditor
                key={i}
                workload={w}
                index={i}
                errors={errors}
                onChange={(next) =>
                  patch({ workloads: model.workloads.map((x, j) => (j === i ? next : x)) })
                }
                onRemove={() => patch({ workloads: model.workloads.filter((_, j) => j !== i) })}
              />
            ))}
          </div>
        )}
      </Section>

      <Section
        title={t("managedServices")}
        description={t("managedServicesHint")}
        action={
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => patch({ managedServices: [...model.managedServices, emptyService()] })}
          >
            <PlusIcon className="size-3.5" /> {t("addService")}
          </Button>
        }
      >
        {model.managedServices.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t("noServices")}</p>
        ) : (
          <div className="flex flex-col gap-3">
            {model.managedServices.map((s, i) => (
              <ServiceEditor
                key={i}
                service={s}
                index={i}
                errors={errors}
                onChange={(next) =>
                  patch({ managedServices: model.managedServices.map((x, j) => (j === i ? next : x)) })
                }
                onRemove={() => patch({ managedServices: model.managedServices.filter((_, j) => j !== i) })}
              />
            ))}
          </div>
        )}
      </Section>

      {preservedKeys.length > 0 && (
        <Section title={t("preserved")} description={t("preservedHint")}>
          <div className="flex flex-wrap gap-1.5">
            {preservedKeys.map((k) => (
              <Badge key={k} variant="outline" className="font-mono text-2xs">
                {k}
              </Badge>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}
