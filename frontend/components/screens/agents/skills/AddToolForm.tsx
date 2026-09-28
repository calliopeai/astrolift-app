"use client";

import { Loader2Icon, PlusIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

import { type Adapter, ADAPTERS } from "./tool-adapters";
import type { ToolDefFields } from "./use-skill-builder";

export interface AddToolFormProps {
  /** Resolves true when the tool registered; the form then calls onDone. */
  onSubmit: (fields: ToolDefFields) => Promise<boolean>;
  loading: boolean;
  onDone: () => void;
}

/** The inline "Register tool definition" form on the skill builder. */
export function AddToolForm({ onSubmit, loading, onDone }: AddToolFormProps) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [adapter, setAdapter] = useState<Adapter>("python_fn");
  const [handlerRef, setHandlerRef] = useState("");
  const [inputSchemaText, setInputSchemaText] = useState("{}");
  const [outputSchemaText, setOutputSchemaText] = useState("{}");

  function autoSlug(value: string) {
    setSlug(
      value
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_|_$/g, "")
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const ok = await onSubmit({
      name,
      slug,
      description,
      adapter,
      handlerRef,
      inputSchemaText,
      outputSchemaText,
    });
    if (ok) onDone();
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4 rounded-lg border p-4">
      <p className="text-sm font-semibold">Register tool definition</p>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <Label className="text-xs">Name</Label>
          <Input
            value={name}
            placeholder="e.g. search_docs"
            className="h-8 text-sm"
            onChange={(e) => {
              setName(e.target.value);
              autoSlug(e.target.value);
            }}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label className="text-xs">Slug</Label>
          <Input
            value={slug}
            placeholder="search_docs"
            className="h-8 font-mono text-sm"
            onChange={(e) => setSlug(e.target.value)}
          />
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <Label className="text-xs">Description</Label>
        <Input
          value={description}
          placeholder="What this tool does"
          className="h-8 text-sm"
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <Label className="text-xs">Adapter</Label>
          <select
            value={adapter}
            onChange={(e) => setAdapter(e.target.value as Adapter)}
            className="border-input bg-background h-8 rounded-md border px-2 text-sm"
          >
            {ADAPTERS.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <Label className="text-xs">Handler ref</Label>
          <Input
            value={handlerRef}
            placeholder="module.function or https://..."
            className="h-8 font-mono text-sm"
            onChange={(e) => setHandlerRef(e.target.value)}
          />
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <Label className="text-xs">Input schema (JSON)</Label>
          <Textarea
            value={inputSchemaText}
            rows={3}
            className="font-mono text-xs"
            onChange={(e) => setInputSchemaText(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label className="text-xs">Output schema (JSON)</Label>
          <Textarea
            value={outputSchemaText}
            rows={3}
            className="font-mono text-xs"
            onChange={(e) => setOutputSchemaText(e.target.value)}
          />
        </div>
      </div>

      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={loading}>
          {loading ? (
            <Loader2Icon className="mr-1 h-3.5 w-3.5 animate-spin" />
          ) : (
            <PlusIcon className="mr-1 h-3.5 w-3.5" />
          )}
          Register tool
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
