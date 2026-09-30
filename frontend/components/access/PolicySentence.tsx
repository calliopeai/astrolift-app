"use client";

import { PlusIcon, XIcon } from "lucide-react";
import * as React from "react";
import { useFormatter, useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { TagInput } from "@/components/ui/tag-input";
import { Textarea } from "@/components/ui/textarea";
import { ASTROLIFT_PERMISSIONS } from "@/lib/permissions/permissions.generated";
import { cn } from "@/lib/utils";

import { localizedConditionError } from "./policy-copy";

import {
  CONDITION_KINDS,
  type ConditionKind,
  newCondition,
  parseCondition,
  type PolicyCondition,
  type PolicyShape,
  RESOURCE_KEYS,
  type ResourceKey,
  serializeCondition,
  WEEKDAYS,
} from "./policy-model";

/** The sign-in factors a session can assert (auth_schemes: otp, webauthn, hwk). */
const FACTORS = ["webauthn", "otp", "hwk"];

export interface PolicySentenceProps {
  policy: PolicyShape;
  /** Edit mode: pickers for every part, the sentence kept above as the preview. */
  onChange?: (next: PolicyShape) => void;
  /** Suggestions for the action; defaults to the permission catalog plus `<resource>.*` globs. */
  actions?: readonly string[];
  className?: string;
}

/**
 * An ABAC policy as a sentence (design 3.6): "Deny app.deploy on anything in
 * production for everyone unless it is Mon to Fri 09:00 to 18:00 UTC." Read
 * mode is the sentence; edit mode (`onChange`) builds it from pickers over
 * the real parts (the action, the resource keys, the actors, one row per
 * condition) with each condition's error beside it, and keeps the raw JSON
 * one click away for kinds the pickers do not cover. Pure.
 */
export function PolicySentence({ policy, onChange, actions, className }: PolicySentenceProps) {
  if (!onChange) {
    return <SentenceText policy={policy} className={className} />;
  }
  return (
    <PolicyEditor policy={policy} onChange={onChange} actions={actions} className={className} />
  );
}

function Literal({ children }: { children: React.ReactNode }) {
  return <span className="bg-muted rounded-sm px-1 font-mono text-xs">{children}</span>;
}

function SentenceText({ policy, className }: { policy: PolicyShape; className?: string }) {
  const t = useTranslations("shared.access.policy");
  const format = useFormatter();
  const list = (values: string[], type: "conjunction" | "disjunction" = "disjunction") =>
    values.length ? (
      <>
        {format.list(
          values.map((value, i) => <Literal key={i}>{value}</Literal>),
          { type }
        )}
      </>
    ) : (
      t("summary.none")
    );
  const joined = (items: React.ReactNode[]) => (
    <>
      {format.list(
        items.map((item, i) => <React.Fragment key={i}>{item}</React.Fragment>),
        { type: "conjunction" }
      )}
    </>
  );
  const resources = RESOURCE_KEYS.filter((key) => policy.resource[key]?.length).map((key) =>
    t.rich("summary.match", {
      kind: t(`resource.${key}`),
      values: () => list(policy.resource[key] ?? []),
    })
  );
  const groups = () => list(policy.actor.groups);
  const role = () => <Literal>{policy.actor.role}</Literal>;
  const actors = policy.actor.groups.length
    ? t.rich(policy.actor.role ? "summary.groupsRole" : "summary.groups", { groups, role })
    : policy.actor.role
      ? t.rich("summary.role", { role })
      : t("summary.everyone");
  const conditions = policy.conditions.map((condition) => {
    switch (condition.kind) {
      case "time_window":
        return t.rich("summary.timeWindow", {
          days: () =>
            list(
              condition.days.map((day) => {
                const index = WEEKDAYS.indexOf(day as (typeof WEEKDAYS)[number]);
                return index < 0
                  ? day
                  : format.dateTime(new Date(Date.UTC(2026, 8, 28 + index)), {
                      weekday: "short",
                      timeZone: "UTC",
                    });
              })
            ),
          hours: () => list(condition.hours),
          tz: condition.tz,
          zone: (chunks) => <Literal>{chunks}</Literal>,
        });
      case "ip_allowlist":
        return t.rich("summary.ip", { ranges: () => list(condition.cidrs) });
      case "approval_required":
        return t("summary.approval", { count: condition.min_approvers });
      case "env_match":
        return t.rich("summary.environment", { values: () => list(condition.env_in) });
      case "device_assertion":
        return t.rich("summary.device", {
          factors: () => list(condition.required_factors, "conjunction"),
        });
      case "freshness":
        return t("summary.freshness", { minutes: condition.max_session_age_minutes });
      case "custom": {
        const raw = condition.raw;
        const name =
          raw && typeof raw === "object" && "kind" in raw && typeof raw.kind === "string"
            ? raw.kind
            : (JSON.stringify(raw) ?? "undefined");
        return t.rich("summary.custom", { name, kind: (chunks) => <Literal>{chunks}</Literal> });
      }
    }
  });
  const action = policy.actionPattern.trim() || "*";
  return (
    <p className={cn("min-w-0 text-sm leading-relaxed [overflow-wrap:anywhere]", className)}>
      {t.rich("summary.rule", {
        effect: () => (
          <span
            className={cn(
              "font-medium",
              policy.effect === "DENY" ? "text-danger-fg" : "text-success-fg"
            )}
          >
            {t(policy.effect === "DENY" ? "deny" : "allow")}
          </span>
        ),
        action: () => (action === "*" ? t("summary.everyAction") : <Literal>{action}</Literal>),
        resource: () => (resources.length ? joined(resources) : t("anything")),
        actor: () => actors,
        conditions: () =>
          conditions.length
            ? t.rich(policy.effect === "DENY" ? "summary.unless" : "summary.onlyWhen", {
                tests: () => joined(conditions),
              })
            : null,
      })}
    </p>
  );
}

function defaultActions(): string[] {
  const resources = [...new Set(ASTROLIFT_PERMISSIONS.map((p) => p.split(".")[0]))];
  return ["*", ...resources.map((r) => `${r}.*`), ...ASTROLIFT_PERMISSIONS];
}

function PolicyEditor({
  policy,
  onChange,
  actions,
  className,
}: {
  policy: PolicyShape;
  onChange: (next: PolicyShape) => void;
  actions?: readonly string[];
  className?: string;
}) {
  const t = useTranslations("shared.access.policy");
  const listId = React.useId();
  const suggestions = React.useMemo(() => actions ?? defaultActions(), [actions]);
  const patch = (next: Partial<PolicyShape>) => onChange({ ...policy, ...next });
  const unused = RESOURCE_KEYS.filter((k) => !(k in policy.resource));

  const setCondition = (i: number, c: PolicyCondition) =>
    patch({ conditions: policy.conditions.map((x, j) => (j === i ? c : x)) });

  return (
    <div className={cn("flex min-w-0 flex-col gap-4", className)}>
      <div className="bg-muted/30 rounded-md border p-3">
        <SentenceText policy={policy} />
      </div>

      <Clause label={t("rule")}>
        <Select
          value={policy.effect}
          onValueChange={(v) => patch({ effect: v as PolicyShape["effect"] })}
        >
          <SelectTrigger aria-label={t("effect")} className="w-28">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="DENY">{t("deny")}</SelectItem>
            <SelectItem value="ALLOW">{t("allow")}</SelectItem>
          </SelectContent>
        </Select>
        <Input
          aria-label={t("action")}
          list={listId}
          value={policy.actionPattern}
          onChange={(e) => patch({ actionPattern: e.target.value })}
          placeholder={t("actionPlaceholder")}
          className="max-w-72 min-w-0 flex-1 font-mono text-xs"
        />
        <datalist id={listId}>
          {suggestions.map((a) => (
            <option key={a} value={a} />
          ))}
        </datalist>
      </Clause>

      <Clause label={t("on")}>
        {Object.keys(policy.resource).length === 0 && (
          <span className="text-muted-foreground text-sm">{t("anything")}</span>
        )}
        <div className="flex w-full min-w-0 flex-col gap-2">
          {RESOURCE_KEYS.filter((k) => k in policy.resource).map((key) => (
            <div key={key} className="flex min-w-0 items-start gap-2">
              <span className="text-muted-foreground w-24 shrink-0 pt-2 text-sm">
                {t(`resource.${key}`)}
              </span>
              <TagInput
                value={policy.resource[key] ?? []}
                onChange={(values) => patch({ resource: { ...policy.resource, [key]: values } })}
                placeholder={
                  key === "env"
                    ? "production"
                    : key === "region"
                      ? "us-west-2"
                      : t("slugPlaceholder")
                }
                className="min-w-0 flex-1 font-mono text-xs"
              />
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={t("removeMatch", { kind: t(`resource.${key}`) })}
                onClick={() => {
                  const { [key]: _gone, ...rest } = policy.resource;
                  patch({ resource: rest });
                }}
              >
                <XIcon />
              </Button>
            </div>
          ))}
        </div>
        {unused.length > 0 && (
          <AddMenu
            label={t("narrow")}
            items={unused.map((k) => ({ key: k, label: t(`resource.${k}`) }))}
            onPick={(k) => patch({ resource: { ...policy.resource, [k as ResourceKey]: [] } })}
          />
        )}
      </Clause>

      <Clause label={t("for")}>
        <div className="flex w-full min-w-0 flex-col gap-2">
          <div className="flex min-w-0 items-start gap-2">
            <span className="text-muted-foreground w-24 shrink-0 pt-2 text-sm">
              {t("inGroups")}
            </span>
            <TagInput
              value={policy.actor.groups}
              onChange={(groups) => patch({ actor: { ...policy.actor, groups } })}
              placeholder={t("groupsPlaceholder")}
              className="min-w-0 flex-1 font-mono text-xs"
            />
          </div>
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-muted-foreground w-24 shrink-0 text-sm">{t("holdingRole")}</span>
            <Input
              aria-label={t("holdingRole")}
              value={policy.actor.role}
              onChange={(e) => patch({ actor: { ...policy.actor, role: e.target.value } })}
              placeholder={t("anyRole")}
              className="max-w-64 min-w-0 flex-1 font-mono text-xs"
            />
          </div>
        </div>
      </Clause>

      <Clause label={t(policy.effect === "DENY" ? "unless" : "onlyWhen")}>
        <div className="flex w-full min-w-0 flex-col gap-3">
          {policy.conditions.length === 0 && (
            <span className="text-muted-foreground text-sm">{t("noConditions")}</span>
          )}
          {policy.conditions.map((c, i) => (
            <ConditionRow
              key={i}
              index={i}
              condition={c}
              onChange={(next) => setCondition(i, next)}
              onRemove={() => patch({ conditions: policy.conditions.filter((_, j) => j !== i) })}
            />
          ))}
          <div className="flex flex-wrap gap-2">
            <AddMenu
              label={t("addCondition")}
              items={CONDITION_KINDS.map((k) => ({ key: k.kind, label: t(`kind.${k.kind}`) }))}
              onPick={(k) =>
                patch({ conditions: [...policy.conditions, newCondition(k as ConditionKind)] })
              }
            />
            <JsonEditor
              conditions={policy.conditions}
              onApply={(conditions) => patch({ conditions })}
            />
          </div>
        </div>
      </Clause>
    </div>
  );
}

function Clause({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start">
      <span className="w-24 shrink-0 pt-1.5 text-sm font-medium">{label}</span>
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

function AddMenu({
  label,
  items,
  onPick,
}: {
  label: string;
  items: { key: string; label: string }[];
  onPick: (key: string) => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" variant="outline" size="sm">
          <PlusIcon />
          {label}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        {items.map((it) => (
          <DropdownMenuItem key={it.key} onSelect={() => onPick(it.key)}>
            {it.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function Toggles({
  label,
  options,
  labelFor,
  value,
  onChange,
}: {
  label: string;
  options: readonly string[];
  labelFor?: (value: string) => string;
  value: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <div role="group" aria-label={label} className="flex min-w-0 flex-wrap gap-1">
      {options.map((o) => {
        const on = value.includes(o);
        return (
          <button
            key={o}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(on ? value.filter((x) => x !== o) : [...value, o])}
            className={cn(
              "focus-visible:ring-ring rounded-full border px-2 py-0.5 font-mono text-xs focus-visible:ring-2 focus-visible:outline-none",
              on ? "border-primary bg-primary/10 text-foreground" : "text-muted-foreground"
            )}
          >
            {labelFor?.(o) ?? o}
          </button>
        );
      })}
    </div>
  );
}

function ConditionRow({
  index,
  condition: c,
  onChange,
  onRemove,
}: {
  index: number;
  condition: PolicyCondition;
  onChange: (next: PolicyCondition) => void;
  onRemove: () => void;
}) {
  const t = useTranslations("shared.access.policy");
  const format = useFormatter();
  const error = localizedConditionError(c, t);
  const kindLabel = t(`kind.${c.kind}`);
  const errorId = `condition-${index}-error`;
  return (
    <div
      className={cn(
        "flex min-w-0 flex-col gap-2 rounded-md border p-3",
        error && "border-danger-border"
      )}
      aria-describedby={error ? errorId : undefined}
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{kindLabel}</span>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={t("removeCondition", { index: index + 1 })}
          onClick={onRemove}
        >
          <XIcon />
        </Button>
      </div>
      {c.kind === "time_window" && (
        <>
          <Toggles
            label={t("days")}
            options={WEEKDAYS}
            labelFor={(day) =>
              format.dateTime(
                new Date(
                  Date.UTC(2026, 8, 28 + WEEKDAYS.indexOf(day as (typeof WEEKDAYS)[number]))
                ),
                { weekday: "short", timeZone: "UTC" }
              )
            }
            value={c.days}
            onChange={(days) => onChange({ ...c, days })}
          />
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <TagInput
              value={c.hours}
              onChange={(hours) => onChange({ ...c, hours })}
              placeholder="09:00-18:00"
              className="max-w-64 min-w-0 flex-1 font-mono text-xs"
            />
            <Input
              aria-label={t("timeZone")}
              value={c.tz}
              onChange={(e) => onChange({ ...c, tz: e.target.value })}
              placeholder="America/Los_Angeles"
              className="max-w-56 min-w-0 flex-1 font-mono text-xs"
            />
          </div>
        </>
      )}
      {c.kind === "ip_allowlist" && (
        <TagInput
          value={c.cidrs}
          onChange={(cidrs) => onChange({ ...c, cidrs })}
          placeholder="10.0.0.0/8"
          className="min-w-0 font-mono text-xs"
        />
      )}
      {c.kind === "approval_required" && (
        <NumberField
          label={t("approvers")}
          value={c.min_approvers}
          onChange={(n) => onChange({ ...c, min_approvers: n })}
        />
      )}
      {c.kind === "env_match" && (
        <TagInput
          value={c.env_in}
          onChange={(env_in) => onChange({ ...c, env_in })}
          placeholder="staging, preview"
          className="min-w-0 font-mono text-xs"
        />
      )}
      {c.kind === "device_assertion" && (
        <Toggles
          label={t("factors")}
          options={FACTORS}
          value={c.required_factors}
          onChange={(required_factors) => onChange({ ...c, required_factors })}
        />
      )}
      {c.kind === "freshness" && (
        <NumberField
          label={t("minutes")}
          value={c.max_session_age_minutes}
          onChange={(n) => onChange({ ...c, max_session_age_minutes: n })}
        />
      )}
      {c.kind === "custom" && (
        <pre className="bg-muted/40 max-h-32 min-w-0 overflow-auto rounded-sm p-2 font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
          {JSON.stringify(c.raw, null, 2)}
        </pre>
      )}
      {error && (
        <p id={errorId} className="text-danger-fg text-xs">
          {error}
        </p>
      )}
    </div>
  );
}

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (n: number) => void;
}) {
  return (
    <label className="flex min-w-0 items-center gap-2 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <Input
        type="number"
        min={1}
        value={Number.isFinite(value) ? value : ""}
        onChange={(e) => onChange(e.target.valueAsNumber)}
        className="w-24 font-mono text-xs"
      />
    </label>
  );
}

function JsonEditor({
  conditions,
  onApply,
}: {
  conditions: PolicyCondition[];
  onApply: (next: PolicyCondition[]) => void;
}) {
  const t = useTranslations("shared.access.policy");
  const [open, setOpen] = React.useState(false);
  const [text, setText] = React.useState("");
  const [error, setError] = React.useState<{ kind: "array" | "parser"; message?: string } | null>(
    null
  );

  if (!open) {
    return (
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => {
          setText(JSON.stringify(conditions.map(serializeCondition), null, 2));
          setError(null);
          setOpen(true);
        }}
      >
        {t("jsonEdit")}
      </Button>
    );
  }
  return (
    <div className="flex w-full min-w-0 flex-col gap-2">
      <Textarea
        aria-label={t("jsonLabel")}
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={8}
        className="font-mono text-xs"
        aria-invalid={Boolean(error)}
      />
      {error && (
        <p className="text-danger-fg text-xs [overflow-wrap:anywhere]">
          {error.kind === "array" ? t("jsonArray") : (error.message ?? t("jsonInvalid"))}
        </p>
      )}
      <div className="flex gap-2">
        <Button
          type="button"
          size="sm"
          onClick={() => {
            try {
              const parsed: unknown = JSON.parse(text.trim() || "[]");
              if (!Array.isArray(parsed)) {
                setError({ kind: "array" });
                return;
              }
              onApply(parsed.map(parseCondition));
              setOpen(false);
            } catch (err) {
              setError({ kind: "parser", message: err instanceof Error ? err.message : undefined });
            }
          }}
        >
          {t("apply")}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
          {t("cancel")}
        </Button>
      </div>
    </div>
  );
}
