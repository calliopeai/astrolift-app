"use client";

/**
 * Controlled field primitives for the visual manifest builder (#1110).
 *
 * These reuse the same `components/ui` inputs that `forms/field-registry`'s
 * widgets are built from and mirror `DynamicField`'s row markup (label +
 * control + help + inline error) for visual parity — but are driven by
 * value/onChange rather than react-hook-form, because the manifest model is
 * deeply nested and array-heavy (the StageBuilder controlled-draft pattern
 * fits it far better than RHF field arrays).
 */

import { PlusIcon, TrashIcon } from "lucide-react";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { TagInput } from "@/components/ui/tag-input";
import type { EnvEntry } from "@/lib/manifest/model";
import type { FieldSpec } from "@/lib/manifest/schema";
import type { TomlValue } from "@/lib/manifest/toml";

export function FieldRow({
  label,
  htmlFor,
  help,
  error,
  children,
}: {
  label: string;
  htmlFor?: string;
  help?: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={htmlFor} className="text-xs">
        {label}
      </Label>
      {children}
      {error && <p className="text-destructive text-xs">{error}</p>}
      {help && !error && <p className="text-muted-foreground text-2xs">{help}</p>}
    </div>
  );
}

export function TextField({
  label,
  value,
  onChange,
  placeholder,
  help,
  error,
  mono,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  help?: string;
  error?: string;
  mono?: boolean;
}) {
  return (
    <FieldRow label={label} help={help} error={error}>
      <Input
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className={mono ? "h-8 font-mono text-xs" : "h-8 text-xs"}
        aria-invalid={!!error}
      />
    </FieldRow>
  );
}

export function NumberField({
  label,
  value,
  onChange,
  placeholder,
  help,
  error,
}: {
  label: string;
  value: number | null;
  onChange: (v: number | null) => void;
  placeholder?: string;
  help?: string;
  error?: string;
}) {
  return (
    <FieldRow label={label} help={help} error={error}>
      <Input
        type="number"
        value={value ?? ""}
        placeholder={placeholder}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === "") return onChange(null);
          const n = Number(raw);
          onChange(Number.isNaN(n) ? null : n);
        }}
        className="h-8 text-xs"
        aria-invalid={!!error}
      />
    </FieldRow>
  );
}

export function ToggleField({
  label,
  value,
  onChange,
  help,
}: {
  label: string;
  value: boolean;
  onChange: (v: boolean) => void;
  help?: string;
}) {
  return (
    <label className="flex items-center gap-2 pt-5 text-xs">
      <input
        type="checkbox"
        checked={value}
        onChange={(e) => onChange(e.target.checked)}
        className="size-4 rounded border"
      />
      <span>{label}</span>
      {help && <span className="text-muted-foreground text-2xs">{help}</span>}
    </label>
  );
}

export function SelectField({
  label,
  value,
  options,
  onChange,
  error,
  help,
}: {
  label: string;
  value: string;
  options: readonly string[];
  onChange: (v: string) => void;
  error?: string;
  help?: string;
}) {
  return (
    <FieldRow label={label} help={help} error={error}>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="h-8 text-xs" aria-invalid={!!error}>
          <SelectValue placeholder={`Select ${label}`} />
        </SelectTrigger>
        <SelectContent>
          {options.map((opt) => (
            <SelectItem key={opt} value={opt}>
              {opt}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </FieldRow>
  );
}

export function ListField({
  label,
  value,
  onChange,
  placeholder,
  help,
}: {
  label: string;
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
  help?: string;
}) {
  return (
    <FieldRow label={label} help={help}>
      <TagInput value={value} onChange={onChange} placeholder={placeholder} />
    </FieldRow>
  );
}

/**
 * Renders one schema-driven field bound to a value bag (a workload's per-kind
 * `extra`). Coercion follows the spec's widget; unset values are removed from
 * the bag so absent keys stay absent in the emitted TOML.
 */
export function SpecField({
  spec,
  bag,
  onPatch,
  error,
}: {
  spec: FieldSpec;
  bag: Record<string, TomlValue>;
  onPatch: (key: string, value: TomlValue | undefined) => void;
  error?: string;
}) {
  const raw = bag[spec.key];
  switch (spec.widget) {
    case "number":
      return (
        <NumberField
          label={spec.label}
          value={typeof raw === "number" ? raw : null}
          placeholder={spec.placeholder}
          help={spec.help}
          error={error}
          onChange={(v) => onPatch(spec.key, v === null ? undefined : v)}
        />
      );
    case "toggle":
      return (
        <ToggleField
          label={spec.label}
          value={raw === true}
          help={spec.help}
          onChange={(v) => onPatch(spec.key, v ? true : undefined)}
        />
      );
    case "select":
      return (
        <SelectField
          label={spec.label}
          value={typeof raw === "string" ? raw : ""}
          options={spec.options ?? []}
          error={error}
          help={spec.help}
          onChange={(v) => onPatch(spec.key, v || undefined)}
        />
      );
    default:
      return (
        <TextField
          label={spec.label}
          value={typeof raw === "string" ? raw : ""}
          placeholder={spec.placeholder}
          help={spec.help}
          error={error}
          onChange={(v) => onPatch(spec.key, v.trim() === "" ? undefined : v)}
        />
      );
  }
}

/**
 * Key/value editor for `[env]` and managed-service `config`. String values are
 * editable inline; non-string values (inline tables such as the managed-service
 * injection form) are preserved and shown read-only so the round-trip stays
 * lossless — the operator edits those in the Code view.
 */
export function KeyValueEditor({
  entries,
  onChange,
  addLabel,
  keyPlaceholder = "KEY",
  valuePlaceholder = "value",
}: {
  entries: EnvEntry[];
  onChange: (next: EnvEntry[]) => void;
  addLabel: string;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
}) {
  const patch = (i: number, next: Partial<EnvEntry>) =>
    onChange(entries.map((e, j) => (j === i ? { ...e, ...next } : e)));
  const remove = (i: number) => onChange(entries.filter((_, j) => j !== i));
  const add = () => onChange([...entries, { key: "", value: "" }]);

  return (
    <div className="flex flex-col gap-2">
      {entries.map((entry, i) => {
        const isString = typeof entry.value === "string";
        return (
          <div key={i} className="flex items-center gap-2">
            <Input
              value={entry.key}
              placeholder={keyPlaceholder}
              onChange={(e) => patch(i, { key: e.target.value })}
              className="h-8 max-w-[40%] font-mono text-xs"
            />
            <span className="text-muted-foreground text-xs">=</span>
            {isString ? (
              <Input
                value={entry.value as string}
                placeholder={valuePlaceholder}
                onChange={(e) => patch(i, { value: e.target.value })}
                className="h-8 flex-1 font-mono text-xs"
              />
            ) : (
              <div className="flex flex-1 items-center gap-2">
                <Badge variant="outline" className="text-2xs">
                  advanced
                </Badge>
                <span className="text-muted-foreground truncate font-mono text-2xs">
                  {JSON.stringify(entry.value)} — edit in Code view
                </span>
              </div>
            )}
            <button
              type="button"
              onClick={() => remove(i)}
              className="text-muted-foreground hover:text-destructive shrink-0"
              aria-label="Remove entry"
            >
              <TrashIcon className="size-3.5" />
            </button>
          </div>
        );
      })}
      <button
        type="button"
        onClick={add}
        className="text-info-fg flex items-center gap-1 self-start text-xs hover:underline"
      >
        <PlusIcon className="size-3" /> {addLabel}
      </button>
    </div>
  );
}
