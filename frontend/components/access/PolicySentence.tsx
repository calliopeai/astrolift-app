"use client";

import { PlusIcon, XIcon } from "lucide-react";
import * as React from "react";

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

import {
  CONDITION_KINDS,
  conditionError,
  type ConditionKind,
  newCondition,
  parseCondition,
  type PolicyCondition,
  type PolicyShape,
  policySegments,
  RESOURCE_KEY_LABEL,
  RESOURCE_KEYS,
  type ResourceKey,
  type Segment,
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
    return (
      <SentenceText
        segments={policySegments(policy)}
        effect={policy.effect}
        className={className}
      />
    );
  }
  return (
    <PolicyEditor policy={policy} onChange={onChange} actions={actions} className={className} />
  );
}

function SentenceText({
  segments,
  effect,
  className,
}: {
  segments: Segment[];
  effect: PolicyShape["effect"];
  className?: string;
}) {
  return (
    <p className={cn("min-w-0 text-sm leading-relaxed [overflow-wrap:anywhere]", className)}>
      {segments.map((s, i) =>
        i === 0 ? (
          <span
            key={i}
            className={cn("font-medium", effect === "DENY" ? "text-danger-fg" : "text-success-fg")}
          >
            {s.text}
          </span>
        ) : s.value ? (
          <span key={i} className="bg-muted rounded-sm px-1 font-mono text-xs">
            {s.text}
          </span>
        ) : (
          <span key={i}>{s.text}</span>
        )
      )}
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
  const listId = React.useId();
  const suggestions = React.useMemo(() => actions ?? defaultActions(), [actions]);
  const patch = (next: Partial<PolicyShape>) => onChange({ ...policy, ...next });
  const unused = RESOURCE_KEYS.filter((k) => !(k in policy.resource));

  const setCondition = (i: number, c: PolicyCondition) =>
    patch({ conditions: policy.conditions.map((x, j) => (j === i ? c : x)) });

  return (
    <div className={cn("flex min-w-0 flex-col gap-4", className)}>
      <div className="bg-muted/30 rounded-md border p-3">
        <SentenceText segments={policySegments(policy)} effect={policy.effect} />
      </div>

      <Clause label="Rule">
        <Select
          value={policy.effect}
          onValueChange={(v) => patch({ effect: v as PolicyShape["effect"] })}
        >
          <SelectTrigger aria-label="Effect" className="w-28">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="DENY">Deny</SelectItem>
            <SelectItem value="ALLOW">Allow</SelectItem>
          </SelectContent>
        </Select>
        <Input
          aria-label="Action"
          list={listId}
          value={policy.actionPattern}
          onChange={(e) => patch({ actionPattern: e.target.value })}
          placeholder="app.deploy, secret.* or *"
          className="max-w-72 min-w-0 flex-1 font-mono text-xs"
        />
        <datalist id={listId}>
          {suggestions.map((a) => (
            <option key={a} value={a} />
          ))}
        </datalist>
      </Clause>

      <Clause label="On">
        {Object.keys(policy.resource).length === 0 && (
          <span className="text-muted-foreground text-sm">anything</span>
        )}
        <div className="flex w-full min-w-0 flex-col gap-2">
          {RESOURCE_KEYS.filter((k) => k in policy.resource).map((key) => (
            <div key={key} className="flex min-w-0 items-start gap-2">
              <span className="text-muted-foreground w-24 shrink-0 pt-2 text-sm">
                {RESOURCE_KEY_LABEL[key]}
              </span>
              <TagInput
                value={policy.resource[key] ?? []}
                onChange={(values) => patch({ resource: { ...policy.resource, [key]: values } })}
                placeholder={
                  key === "env" ? "production" : key === "region" ? "us-west-2" : "slug or glob"
                }
                className="min-w-0 flex-1 font-mono text-xs"
              />
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={`Remove the ${RESOURCE_KEY_LABEL[key]} match`}
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
            label="Narrow to"
            items={unused.map((k) => ({ key: k, label: RESOURCE_KEY_LABEL[k] }))}
            onPick={(k) => patch({ resource: { ...policy.resource, [k as ResourceKey]: [] } })}
          />
        )}
      </Clause>

      <Clause label="For">
        <div className="flex w-full min-w-0 flex-col gap-2">
          <div className="flex min-w-0 items-start gap-2">
            <span className="text-muted-foreground w-24 shrink-0 pt-2 text-sm">in groups</span>
            <TagInput
              value={policy.actor.groups}
              onChange={(groups) => patch({ actor: { ...policy.actor, groups } })}
              placeholder="everyone, or IdP groups"
              className="min-w-0 flex-1 font-mono text-xs"
            />
          </div>
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-muted-foreground w-24 shrink-0 text-sm">holding role</span>
            <Input
              aria-label="Holding role"
              value={policy.actor.role}
              onChange={(e) => patch({ actor: { ...policy.actor, role: e.target.value } })}
              placeholder="any role"
              className="max-w-64 min-w-0 flex-1 font-mono text-xs"
            />
          </div>
        </div>
      </Clause>

      <Clause label={policy.effect === "DENY" ? "Unless" : "Only when"}>
        <div className="flex w-full min-w-0 flex-col gap-3">
          {policy.conditions.length === 0 && (
            <span className="text-muted-foreground text-sm">
              no conditions: it applies to every request
            </span>
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
              label="Add condition"
              items={CONDITION_KINDS.map((k) => ({ key: k.kind, label: k.label }))}
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
  value,
  onChange,
}: {
  label: string;
  options: readonly string[];
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
            {o}
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
  const error = conditionError(c);
  const kindLabel =
    c.kind === "custom" ? "custom" : CONDITION_KINDS.find((k) => k.kind === c.kind)?.label;
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
          aria-label={`Remove condition ${index + 1}`}
          onClick={onRemove}
        >
          <XIcon />
        </Button>
      </div>
      {c.kind === "time_window" && (
        <>
          <Toggles
            label="Days"
            options={WEEKDAYS}
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
              aria-label="Time zone"
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
          label="Approvers"
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
          label="Factors"
          options={FACTORS}
          value={c.required_factors}
          onChange={(required_factors) => onChange({ ...c, required_factors })}
        />
      )}
      {c.kind === "freshness" && (
        <NumberField
          label="Minutes since sign-in"
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
  const [open, setOpen] = React.useState(false);
  const [text, setText] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

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
        Edit as JSON
      </Button>
    );
  }
  return (
    <div className="flex w-full min-w-0 flex-col gap-2">
      <Textarea
        aria-label="Conditions as JSON"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={8}
        className="font-mono text-xs"
        aria-invalid={Boolean(error)}
      />
      {error && <p className="text-danger-fg text-xs [overflow-wrap:anywhere]">{error}</p>}
      <div className="flex gap-2">
        <Button
          type="button"
          size="sm"
          onClick={() => {
            try {
              const parsed: unknown = JSON.parse(text.trim() || "[]");
              if (!Array.isArray(parsed)) throw new Error("Conditions are a JSON array.");
              onApply(parsed.map(parseCondition));
              setOpen(false);
            } catch (err) {
              setError(err instanceof Error ? err.message : "Invalid JSON");
            }
          }}
        >
          Apply
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
