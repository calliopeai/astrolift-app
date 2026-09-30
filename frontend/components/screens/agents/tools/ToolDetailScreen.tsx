"use client";

import {
  AlertTriangleIcon,
  BracesIcon,
  FileTextIcon,
  Loader2Icon,
  MoreHorizontalIcon,
  PlugIcon,
  ServerCrashIcon,
  TrashIcon,
} from "lucide-react";
import { useRef, useState } from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { agentsCrumbs } from "@/components/screens/agents/skills/catalog";
import {
  ADAPTER_LABEL,
  ADAPTERS,
  HANDLER_PLACEHOLDER,
} from "@/components/screens/agents/skills/tool-adapters";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";

import type { ToolAdapter, ToolErrors, useToolDetail } from "./use-tool-detail";

export type ToolDetailScreenProps = ReturnType<typeof useToolDetail> & {
  /** Errors to open with; stories use it. */
  initialErrors?: ToolErrors;
};

type ToolField = Exclude<keyof ToolErrors, "form">;

const FORM_ID = "tool-detail-form";

function prettyJson(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return "{}";
  }
}

/**
 * Agents › Tools › one tool (spec 44 §5.2): its identity, adapter and
 * handler, and schemas on Panels, saved from the title row, with Delete in
 * `⋯`. Errors stand beside their fields. Holds the form values;
 * useToolDetail owns the query and mutations.
 */
export function ToolDetailScreen({
  tool,
  loading,
  error,
  onRetry,
  saving,
  deleting,
  save,
  remove,
  initialErrors = {},
}: ToolDetailScreenProps) {
  const [name, setName] = useState(tool?.name ?? "");
  const [slug, setSlug] = useState(tool?.slug ?? "");
  const [description, setDescription] = useState(tool?.description ?? "");
  const [adapter, setAdapter] = useState<ToolAdapter>(
    (tool?.adapter as ToolAdapter) || "python_fn"
  );
  const [handlerRef, setHandlerRef] = useState(tool?.handlerRef ?? "");
  const [inputSchemaText, setInputSchemaText] = useState(prettyJson(tool?.inputSchema ?? {}));
  const [outputSchemaText, setOutputSchemaText] = useState(prettyJson(tool?.outputSchema ?? {}));
  const [errors, setErrors] = useState<ToolErrors>(initialErrors);
  const [dirty, setDirty] = useState(false);
  const editVersion = useRef(0);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const [sourceRecord, setSourceRecord] = useState(tool);
  if (
    JSON.stringify(tool) !== JSON.stringify(sourceRecord) &&
    (!dirty || tool?.id !== sourceRecord?.id)
  ) {
    setSourceRecord(tool);
    if (tool && (!dirty || tool.id !== sourceRecord?.id)) {
      setName(tool.name);
      setSlug(tool.slug);
      setDescription(tool.description);
      setAdapter((tool.adapter as ToolAdapter) || "python_fn");
      setHandlerRef(tool.handlerRef);
      setInputSchemaText(prettyJson(tool.inputSchema));
      setOutputSchemaText(prettyJson(tool.outputSchema));
      if (tool.id !== sourceRecord?.id) setDirty(false);
    }
  }

  function edit<T>(set: (v: T) => void, field: ToolField) {
    return (v: T) => {
      editVersion.current += 1;
      set(v);
      setDirty(true);
      setErrors((e) => ({ ...e, [field]: undefined, form: undefined }));
    };
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    const version = editVersion.current;
    const found = await save({
      name,
      slug,
      description,
      adapter,
      handlerRef,
      inputSchemaText,
      outputSchemaText,
    });
    setErrors(found);
    if (editVersion.current === version && Object.values(found).every((v) => !v)) setDirty(false);
  }

  if (!tool) {
    const pending = loading && !error;
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <ShellHeader
          crumbs={agentsCrumbs("tools", { label: pending ? "Loading" : "Not found" })}
          title={pending ? <Skeleton className="h-6 w-48" /> : "Tool not found"}
        />
        {pending ? (
          <div className="grid min-w-0 grid-cols-12 gap-4" aria-busy>
            <Skeleton className="col-span-12 h-40 w-full xl:col-span-6" />
            <Skeleton className="col-span-12 h-40 w-full xl:col-span-6" />
            <Skeleton className="col-span-12 h-56 w-full" />
          </div>
        ) : error ? (
          <div
            role="alert"
            className="flex flex-col items-center gap-3 rounded-md border py-10 text-center"
          >
            <ServerCrashIcon className="text-danger size-5" aria-hidden />
            <div className="min-w-0 px-6">
              <p className="font-medium">Couldn&apos;t load this tool</p>
              <p className="text-muted-foreground mt-1 max-w-md font-mono text-xs [overflow-wrap:anywhere]">
                {error.message}
              </p>
            </div>
            <Button size="sm" variant="outline" onClick={onRetry}>
              Retry
            </Button>
          </div>
        ) : (
          <EmptyState
            icon={<AlertTriangleIcon className="size-5" />}
            title="Tool not found"
            description="This tool may have been deleted, or you may not have access to it."
            actionHref="/agents/tools"
            actionLabel="Back to tools"
          />
        )}
      </div>
    );
  }

  const invalid = (k: ToolField) => Boolean(errors[k]) || undefined;
  const fieldError = (k: ToolField) => (
    <FieldError className="[overflow-wrap:anywhere]">{errors[k]}</FieldError>
  );

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ShellHeader
        crumbs={agentsCrumbs("tools", { label: tool.name })}
        title={<span title={tool.name}>{tool.name}</span>}
        status={
          <Badge variant="secondary" className="shrink-0">
            {ADAPTER_LABEL[tool.adapter] ?? (tool.adapter || "unknown")}
          </Badge>
        }
        context={
          <span className="font-mono text-xs" title={tool.slug}>
            {tool.slug}
          </span>
        }
        primaryAction={
          <>
            {dirty && <span className="text-muted-foreground text-xs">Unsaved changes</span>}
            <Button type="submit" form={FORM_ID} size="sm" disabled={saving || !dirty}>
              {saving && <Loader2Icon className="size-4 animate-spin" />}
              Save tool
            </Button>
          </>
        }
        menu={
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="icon" variant="ghost" className="size-8" aria-label="More actions">
                <MoreHorizontalIcon className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-48">
              <DropdownMenuItem
                variant="destructive"
                disabled={deleting}
                onSelect={() => setConfirmDelete(true)}
              >
                <TrashIcon className="size-4" />
                Delete tool
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        }
      />

      <form id={FORM_ID} onSubmit={handleSave} noValidate className="flex min-w-0 flex-col gap-4">
        {errors.form && (
          <div
            role="alert"
            className="border-destructive/40 bg-destructive/5 text-destructive flex min-w-0 items-start gap-2 rounded-md border p-3 text-sm"
          >
            <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span className="min-w-0 [overflow-wrap:anywhere]">{errors.form}</span>
          </div>
        )}
        <PanelGrid>
          <Panel title="Identity" icon={<FileTextIcon className="size-4" />} span={6}>
            <div className="flex min-w-0 flex-col gap-4">
              <div className="grid min-w-0 gap-4 sm:grid-cols-2">
                <Field data-invalid={invalid("name")} className="min-w-0">
                  <FieldLabel htmlFor="tool-name">Name</FieldLabel>
                  <Input
                    id="tool-name"
                    value={name}
                    aria-invalid={invalid("name")}
                    onChange={(e) => edit(setName, "name")(e.target.value)}
                  />
                  {fieldError("name")}
                </Field>
                <Field data-invalid={invalid("slug")} className="min-w-0">
                  <FieldLabel htmlFor="tool-slug">Slug</FieldLabel>
                  <Input
                    id="tool-slug"
                    value={slug}
                    className="font-mono"
                    spellCheck={false}
                    aria-invalid={invalid("slug")}
                    onChange={(e) => edit(setSlug, "slug")(e.target.value)}
                  />
                  {fieldError("slug")}
                </Field>
              </div>
              <Field data-invalid={invalid("description")} className="min-w-0">
                <FieldLabel htmlFor="tool-description">Description</FieldLabel>
                <Input
                  id="tool-description"
                  value={description}
                  placeholder="What this tool does"
                  aria-invalid={invalid("description")}
                  onChange={(e) => edit(setDescription, "description")(e.target.value)}
                />
                {fieldError("description")}
              </Field>
            </div>
          </Panel>

          <Panel title="Handler" icon={<PlugIcon className="size-4" />} span={6}>
            <div className="flex min-w-0 flex-col gap-4">
              <Field data-invalid={invalid("adapter")} className="min-w-0">
                <FieldLabel htmlFor="tool-adapter">Adapter</FieldLabel>
                <Select
                  value={adapter}
                  onValueChange={(v) => edit(setAdapter, "adapter")(v as ToolAdapter)}
                >
                  <SelectTrigger
                    id="tool-adapter"
                    className="w-full min-w-0"
                    aria-invalid={invalid("adapter")}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ADAPTERS.map((a) => (
                      <SelectItem key={a.value} value={a.value}>
                        {a.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {fieldError("adapter")}
              </Field>
              <Field data-invalid={invalid("handlerRef")} className="min-w-0">
                <FieldLabel htmlFor="tool-handler">Handler ref</FieldLabel>
                <Input
                  id="tool-handler"
                  value={handlerRef}
                  placeholder={HANDLER_PLACEHOLDER[adapter]}
                  className="font-mono"
                  spellCheck={false}
                  aria-invalid={invalid("handlerRef")}
                  onChange={(e) => edit(setHandlerRef, "handlerRef")(e.target.value)}
                />
                {fieldError("handlerRef")}
              </Field>
            </div>
          </Panel>

          <Panel
            title="Input schema"
            icon={<BracesIcon className="size-4" />}
            description="JSON Schema for what the agent passes in."
            span={6}
          >
            <Field data-invalid={invalid("inputSchema")} className="min-w-0">
              <FieldLabel htmlFor="tool-input" className="sr-only">
                Input schema (JSON Schema)
              </FieldLabel>
              <Textarea
                id="tool-input"
                value={inputSchemaText}
                rows={10}
                className="max-h-96 font-mono text-xs"
                aria-invalid={invalid("inputSchema")}
                onChange={(e) => edit(setInputSchemaText, "inputSchema")(e.target.value)}
              />
              {fieldError("inputSchema")}
            </Field>
          </Panel>

          <Panel
            title="Output schema"
            icon={<BracesIcon className="size-4" />}
            description="JSON Schema for what the tool returns."
            span={6}
          >
            <Field data-invalid={invalid("outputSchema")} className="min-w-0">
              <FieldLabel htmlFor="tool-output" className="sr-only">
                Output schema (JSON Schema)
              </FieldLabel>
              <Textarea
                id="tool-output"
                value={outputSchemaText}
                rows={10}
                className="max-h-96 font-mono text-xs"
                aria-invalid={invalid("outputSchema")}
                onChange={(e) => edit(setOutputSchemaText, "outputSchema")(e.target.value)}
              />
              {fieldError("outputSchema")}
            </Field>
          </Panel>
        </PanelGrid>
      </form>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`Delete tool "${tool.name}"?`}
        description="This cannot be undone. Skills and agents that call this tool lose the capability on their next run."
        confirmLabel="Delete tool"
        destructive
        onConfirm={remove}
      />
    </div>
  );
}
