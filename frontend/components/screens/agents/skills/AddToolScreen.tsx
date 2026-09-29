"use client";

import { AlertTriangleIcon, ArrowLeftIcon, ArrowRightIcon, Loader2Icon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

import { agentsCrumbs } from "./catalog";
import { FlowSteps } from "./FlowSteps";
import { skillTabHref } from "./SkillFrame";
import { type Adapter, ADAPTERS, HANDLER_PLACEHOLDER } from "./tool-adapters";
import {
  type AddToolState,
  TOOL_STEP,
  type ToolErrors,
  type ToolField,
  validateTool,
} from "./use-add-tool";

export type AddToolScreenProps = AddToolState & {
  /** Start on a step; stories use it. */
  initialStep?: 1 | 2 | 3;
  /** Errors to open with; stories use it. */
  initialErrors?: ToolErrors;
};

const STEPS = [{ label: "Tool" }, { label: "Handler" }, { label: "Schemas" }];

const slugify = (value: string) =>
  value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_|_$/g, "");

/**
 * Agents › Skills › skill › Register tool: seven fields, so a page in three
 * steps (spec 44 §5.4). Errors stand beside their fields, and a refusal
 * opens the step that holds its field; the outcome is the hook's toast.
 * Pure view; the data half is useAddTool.
 */
export function AddToolScreen({
  skillId,
  skill,
  creating,
  createTool,
  initialStep = 1,
  initialErrors = {},
}: AddToolScreenProps) {
  const [step, setStep] = React.useState<1 | 2 | 3>(initialStep);
  const [name, setName] = React.useState("");
  const [slug, setSlug] = React.useState("");
  const [slugTouched, setSlugTouched] = React.useState(false);
  const [description, setDescription] = React.useState("");
  const [adapter, setAdapter] = React.useState<Adapter>("python_fn");
  const [handlerRef, setHandlerRef] = React.useState("");
  const [inputSchemaText, setInputSchemaText] = React.useState("{}");
  const [outputSchemaText, setOutputSchemaText] = React.useState("{}");
  const [errors, setErrors] = React.useState<ToolErrors>(initialErrors);

  const toolsHref = skillTabHref(skillId, "tools");
  const fields = {
    name,
    slug,
    description,
    adapter,
    handlerRef,
    inputSchemaText,
    outputSchemaText,
  };

  const clear = (field: ToolField) =>
    setErrors((e) => ({ ...e, [field]: undefined, form: undefined }));

  /** The first step with an error, or none. */
  function stepWithError(found: ToolErrors): 1 | 2 | 3 | null {
    const steps = (Object.keys(found) as (ToolField | "form")[])
      .filter((k) => k !== "form" && found[k])
      .map((k) => TOOL_STEP[k as ToolField]);
    return steps.length > 0 ? (Math.min(...steps) as 1 | 2 | 3) : null;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (step < 3) {
      // Continue checks only the fields on the steps done so far.
      const found = validateTool(fields);
      const here = (Object.keys(found) as ToolField[]).filter((k) => TOOL_STEP[k] <= step);
      if (here.length > 0) {
        setErrors(Object.fromEntries(here.map((k) => [k, found[k]])));
        return;
      }
      setStep((step + 1) as 2 | 3);
      return;
    }
    const found = await createTool(fields);
    setErrors(found);
    const at = stepWithError(found);
    if (at) setStep(at);
  }

  function field(
    id: string,
    key: ToolField,
    label: string,
    control: React.ReactNode,
    help?: React.ReactNode
  ) {
    return (
      <Field data-invalid={Boolean(errors[key]) || undefined} className="min-w-0">
        <FieldLabel htmlFor={id}>{label}</FieldLabel>
        {control}
        <FieldError className="[overflow-wrap:anywhere]">{errors[key]}</FieldError>
        {help && <FieldDescription>{help}</FieldDescription>}
      </Field>
    );
  }

  const invalid = (key: ToolField) => Boolean(errors[key]) || undefined;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ShellHeader
        crumbs={agentsCrumbs(
          "skills",
          { label: skill?.name ?? "Skill", href: skillTabHref(skillId, "builder") },
          { label: "Register tool" }
        )}
        title="Register tool"
        context={<FlowSteps steps={STEPS} current={step} />}
      />

      <form
        onSubmit={submit}
        noValidate
        className="bg-card flex max-w-3xl min-w-0 flex-col gap-4 rounded-md border p-6"
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
            <div className="grid min-w-0 gap-4 sm:grid-cols-2">
              {field(
                "tool-name",
                "name",
                "Name",
                <Input
                  id="tool-name"
                  value={name}
                  placeholder="e.g. search_docs"
                  aria-invalid={invalid("name")}
                  onChange={(e) => {
                    setName(e.target.value);
                    clear("name");
                    if (!slugTouched) setSlug(slugify(e.target.value));
                  }}
                />
              )}
              {field(
                "tool-slug",
                "slug",
                "Slug",
                <Input
                  id="tool-slug"
                  value={slug}
                  placeholder="search_docs"
                  className="font-mono"
                  spellCheck={false}
                  aria-invalid={invalid("slug")}
                  onChange={(e) => {
                    setSlug(e.target.value);
                    setSlugTouched(true);
                    clear("slug");
                  }}
                />
              )}
            </div>
            {field(
              "tool-description",
              "description",
              "Description",
              <Input
                id="tool-description"
                value={description}
                placeholder="What this tool does"
                aria-invalid={invalid("description")}
                onChange={(e) => {
                  setDescription(e.target.value);
                  clear("description");
                }}
              />
            )}
          </>
        )}

        {step === 2 && (
          <div className="grid min-w-0 gap-4 sm:grid-cols-2">
            {field(
              "tool-adapter",
              "adapter",
              "Adapter",
              <Select
                value={adapter}
                onValueChange={(v) => {
                  setAdapter(v as Adapter);
                  clear("adapter");
                }}
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
            )}
            {field(
              "tool-handler",
              "handlerRef",
              "Handler ref",
              <Input
                id="tool-handler"
                value={handlerRef}
                placeholder={HANDLER_PLACEHOLDER[adapter]}
                className="font-mono"
                spellCheck={false}
                aria-invalid={invalid("handlerRef")}
                onChange={(e) => {
                  setHandlerRef(e.target.value);
                  clear("handlerRef");
                }}
              />,
              "A dotted Python path, an HTTP URL or an MCP server address, by adapter."
            )}
          </div>
        )}

        {step === 3 && (
          <div className="grid min-w-0 gap-4 sm:grid-cols-2">
            {field(
              "tool-input",
              "inputSchema",
              "Input schema (JSON)",
              <Textarea
                id="tool-input"
                value={inputSchemaText}
                rows={8}
                className="max-h-96 font-mono text-xs"
                aria-invalid={invalid("inputSchema")}
                onChange={(e) => {
                  setInputSchemaText(e.target.value);
                  clear("inputSchema");
                }}
              />
            )}
            {field(
              "tool-output",
              "outputSchema",
              "Output schema (JSON)",
              <Textarea
                id="tool-output"
                value={outputSchemaText}
                rows={8}
                className="max-h-96 font-mono text-xs"
                aria-invalid={invalid("outputSchema")}
                onChange={(e) => {
                  setOutputSchemaText(e.target.value);
                  clear("outputSchema");
                }}
              />
            )}
          </div>
        )}

        <div className="flex min-w-0 flex-wrap items-center justify-end gap-2 border-t pt-4">
          <Button type="button" variant="ghost" asChild className="mr-auto">
            <Link href={toolsHref}>Cancel</Link>
          </Button>
          {step > 1 && (
            <Button type="button" variant="outline" onClick={() => setStep((step - 1) as 1 | 2)}>
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
            <Button type="submit" disabled={creating}>
              {creating && <Loader2Icon className="size-4 animate-spin" />}
              {creating ? "Registering…" : "Register tool"}
            </Button>
          )}
        </div>
      </form>
    </div>
  );
}
