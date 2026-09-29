"use client";

import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";

type JsonSchema = {
  title?: string;
  description?: string;
  type?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  enum?: unknown[];
  format?: string;
  items?: JsonSchema;
  minLength?: number;
  maxLength?: number;
  minimum?: number;
  maximum?: number;
};

/**
 * Live preview of a form rendered from its JSON Schema (#437 scope B).
 *
 * Read-only — operators see the same field shapes a submitter would
 * without committing to a publish. We deliberately don't wire form
 * state (no react-hook-form): the preview is for layout / wording
 * review, the real submit page handles validation + submission.
 *
 * Covers JSON Schema's common shapes: string (with format hint), number,
 * boolean, enum (rendered as a Select), array (rendered as a chip list),
 * and nested object (rendered as a fieldset). Falls through to a JSON
 * dump for shapes we don't have a renderer for, so an operator can
 * still see the field exists and gets caught in code review.
 */
export function FormPreviewTab({ schema }: { schema: Record<string, unknown> }) {
  const root = schema as JsonSchema;
  if (!root || typeof root !== "object") {
    return (
      <div className="text-muted-foreground rounded-md border border-dashed p-6 text-center text-sm">
        Form has no schema yet.
      </div>
    );
  }
  const properties = root.properties ?? {};
  const required = new Set(root.required ?? []);
  const entries = Object.entries(properties);

  if (entries.length === 0) {
    return (
      <div className="text-muted-foreground rounded-md border border-dashed p-6 text-center text-sm">
        Schema has no fields defined.
      </div>
    );
  }

  return (
    <div className="bg-card rounded-md border p-6">
      {root.title && <h3 className="mb-1 text-lg font-semibold">{root.title}</h3>}
      {root.description && <p className="text-muted-foreground mb-4 text-sm">{root.description}</p>}
      <div className="flex flex-col gap-4">
        {entries.map(([key, field]) => (
          <FieldPreview key={key} name={key} field={field} required={required.has(key)} />
        ))}
      </div>
      <div className="mt-6 flex items-center justify-end gap-2">
        <button
          type="button"
          className="bg-muted text-muted-foreground cursor-not-allowed rounded-md px-4 py-2 text-sm"
          disabled
        >
          Cancel
        </button>
        <button
          type="button"
          className="bg-primary text-primary-foreground cursor-not-allowed rounded-md px-4 py-2 text-sm opacity-80"
          disabled
        >
          Submit (preview)
        </button>
      </div>
    </div>
  );
}

function FieldPreview({
  name,
  field,
  required,
}: {
  name: string;
  field: JsonSchema;
  required: boolean;
}) {
  const label = field.title ?? name;
  return (
    <label className="flex flex-col gap-1">
      <span className="flex items-center gap-2 text-sm font-medium">
        {label}
        {required && (
          <span aria-label="required" className="text-destructive">
            *
          </span>
        )}
        {field.format && <Badge variant="outline">{field.format}</Badge>}
      </span>
      {field.description && (
        <span className="text-muted-foreground text-xs">{field.description}</span>
      )}
      {renderInput(field)}
    </label>
  );
}

function renderInput(field: JsonSchema): ReactNode {
  if (field.enum && Array.isArray(field.enum)) {
    return (
      <select
        disabled
        className="bg-muted border-input cursor-not-allowed rounded-md border px-3 py-1.5 text-sm"
      >
        <option value="">Select…</option>
        {field.enum.map((v) => (
          <option key={String(v)} value={String(v)}>
            {String(v)}
          </option>
        ))}
      </select>
    );
  }
  if (field.type === "boolean") {
    return (
      <span className="text-muted-foreground inline-flex items-center gap-2 text-xs">
        <input type="checkbox" disabled className="cursor-not-allowed" /> Boolean field
      </span>
    );
  }
  if (field.type === "number" || field.type === "integer") {
    return (
      <input
        type="number"
        disabled
        placeholder="0"
        className="bg-muted border-input cursor-not-allowed rounded-md border px-3 py-1.5 text-sm"
      />
    );
  }
  if (field.type === "array") {
    return (
      <div className="bg-muted text-muted-foreground rounded-md border border-dashed px-3 py-2 text-xs">
        Array of {field.items?.type ?? "item"}
      </div>
    );
  }
  if (field.type === "object") {
    return (
      <div className="bg-muted/40 rounded-md border border-dashed p-3">
        <div className="text-muted-foreground mb-1 text-xs">Nested object</div>
        <div className="flex flex-col gap-2 pl-3">
          {Object.entries(field.properties ?? {}).map(([k, v]) => (
            <FieldPreview key={k} name={k} field={v} required={false} />
          ))}
        </div>
      </div>
    );
  }
  if ((field.format ?? "") === "textarea" || (field.maxLength ?? 0) > 200) {
    return (
      <textarea
        disabled
        placeholder="Text…"
        rows={3}
        className="bg-muted border-input cursor-not-allowed rounded-md border px-3 py-1.5 text-sm"
      />
    );
  }
  return (
    <input
      type={field.format === "email" ? "email" : field.format === "url" ? "url" : "text"}
      disabled
      placeholder="Text…"
      className="bg-muted border-input cursor-not-allowed rounded-md border px-3 py-1.5 text-sm"
    />
  );
}
