"use client";

import {
  ChevronRightIcon,
  CodeIcon,
  Loader2Icon,
  PlusIcon,
  SparklesIcon,
  TrashIcon,
  WrenchIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";

import { AddToolForm } from "./AddToolForm";
import { ADAPTERS } from "./tool-adapters";
import type { SkillBuilderState, ToolDef } from "./use-skill-builder";

export type SkillBuilderScreenProps = SkillBuilderState;

/**
 * The skill builder: edit a skill's name, slug, description and content,
 * and register or remove its tool definitions. Holds the form values and
 * which dialog is open; useSkillBuilder owns every query and mutation.
 */
export function SkillBuilderScreen({
  skill,
  tools,
  skillLoading,
  toolsLoading,
  errorMessage,
  saving,
  deleting,
  deletingTool,
  creatingTool,
  aiAssisting,
  saveSkill,
  deleteSkill,
  deleteTool,
  createTool,
  aiAssist,
}: SkillBuilderScreenProps) {
  // ── form state ──────────────────────────────────────────────────────────────
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [content, setContent] = useState("");
  const [showAddTool, setShowAddTool] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [confirmDeleteSkill, setConfirmDeleteSkill] = useState(false);
  const [toolToRemove, setToolToRemove] = useState<ToolDef | null>(null);

  // Seed form when skill loads
  useEffect(() => {
    if (skill && !dirty) {
      setName(skill.name);
      setSlug(skill.slug);
      setDescription(skill.description);
      setContent(skill.content);
    }
  }, [skill, dirty]);

  // ── handlers ────────────────────────────────────────────────────────────────

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (await saveSkill({ name, slug, description, content })) {
      setDirty(false);
    }
  }

  async function handleAiAssist() {
    const generated = await aiAssist({ name, description, content });
    if (generated) {
      setContent(generated);
      setDirty(true);
    }
  }

  // ── loading / error states ───────────────────────────────────────────────────

  if (skillLoading && !skill) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <Loader2Icon className="text-muted-foreground h-6 w-6 animate-spin" />
      </div>
    );
  }

  if (errorMessage !== null || !skill) {
    return (
      <PageShell title="Skill not found">
        <EmptyState
          icon={<SparklesIcon className="size-5" />}
          title={errorMessage !== null ? "Couldn't load this skill" : "Skill not found"}
          description={
            errorMessage !== null
              ? errorMessage
              : "This skill may have been deleted, or you may not have access to it."
          }
          actionHref="/agents/skills"
          actionLabel="Back to skills"
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={
        <span className="flex items-center gap-2">
          <Link href="/agents/skills" className="text-muted-foreground hover:text-foreground">
            Skills
          </Link>
          <ChevronRightIcon className="text-muted-foreground h-4 w-4" />
          {skill.name}
        </span>
      }
      description={
        <span className="flex items-center gap-2">
          <span className="font-mono text-xs">{skill.slug}</span>
          <Badge variant={skill.isActive ? "default" : "secondary"} className="text-xs">
            {skill.isActive ? "Active" : "Inactive"}
          </Badge>
          {skill.isGlobal && (
            <Badge variant="outline" className="text-xs">
              Global
            </Badge>
          )}
          <span className="text-muted-foreground text-xs">v{skill.skillVersion}</span>
        </span>
      }
      actions={
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setConfirmDeleteSkill(true)}
          disabled={deleting}
          className="text-destructive hover:text-destructive"
        >
          {deleting ? (
            <Loader2Icon className="mr-1 h-3.5 w-3.5 animate-spin" />
          ) : (
            <TrashIcon className="mr-1 h-3.5 w-3.5" />
          )}
          Delete
        </Button>
      }
    >
      {/* ── Skill form ──────────────────────────────────────────────────────── */}
      <form onSubmit={handleSave} className="flex flex-col gap-6">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label>Name</Label>
            <Input
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setDirty(true);
              }}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Slug</Label>
            <Input
              value={slug}
              className="font-mono"
              onChange={(e) => {
                setSlug(e.target.value);
                setDirty(true);
              }}
            />
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label>Description</Label>
          <Input
            value={description}
            placeholder="Short description shown in search and picker"
            onChange={(e) => {
              setDescription(e.target.value);
              setDirty(true);
            }}
          />
        </div>

        <Separator />

        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between">
            <Label>Instructions / content</Label>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleAiAssist}
              disabled={aiAssisting}
            >
              {aiAssisting ? (
                <Loader2Icon className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : (
                <SparklesIcon className="mr-1 h-3.5 w-3.5" />
              )}
              AI assist
            </Button>
          </div>
          <Textarea
            value={content}
            placeholder="System prompt / instructions injected into the agent brief when this skill is active"
            rows={10}
            className="font-mono text-sm"
            onChange={(e) => {
              setContent(e.target.value);
              setDirty(true);
            }}
          />
        </div>

        <div className="flex items-center gap-2">
          <Button type="submit" disabled={saving || !dirty} size="sm">
            {saving ? <Loader2Icon className="mr-1 h-3.5 w-3.5 animate-spin" /> : null}
            Save skill
          </Button>
          {dirty && <span className="text-muted-foreground text-xs">Unsaved changes</span>}
        </div>
      </form>

      <Separator />

      {/* ── Tool definitions ────────────────────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">
            Tool definitions
            <Badge variant="outline" className="ml-2 text-xs">
              {tools.length}
            </Badge>
          </h2>
          <Button variant="outline" size="sm" onClick={() => setShowAddTool((v) => !v)}>
            <PlusIcon className="mr-1 h-3.5 w-3.5" />
            {showAddTool ? "Cancel" : "Register tool"}
          </Button>
        </div>

        {showAddTool && (
          <AddToolForm
            onSubmit={createTool}
            loading={creatingTool}
            onDone={() => setShowAddTool(false)}
          />
        )}

        {toolsLoading && tools.length === 0 && (
          <Loader2Icon className="text-muted-foreground h-4 w-4 animate-spin" />
        )}

        {!toolsLoading && tools.length === 0 && !showAddTool && (
          <EmptyState
            icon={<CodeIcon className="size-5" />}
            title="No tool definitions"
            description="Register tool definitions to give this skill executable capabilities."
            secondary={
              <Button size="sm" onClick={() => setShowAddTool(true)}>
                <PlusIcon className="mr-1 h-3.5 w-3.5" /> Register first tool
              </Button>
            }
          />
        )}

        {tools.length > 0 && (
          <div className="flex flex-col gap-2">
            {tools.map((tool) => (
              <div
                key={tool.id}
                className="flex items-center gap-4 rounded-md border px-4 py-3 text-sm"
              >
                <WrenchIcon className="text-muted-foreground h-4 w-4 shrink-0" />
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="font-medium">{tool.name}</span>
                  {tool.description && (
                    <span className="text-muted-foreground truncate text-xs">
                      {tool.description}
                    </span>
                  )}
                </div>
                <Badge variant="secondary" className="shrink-0 text-xs">
                  {ADAPTERS.find((a) => a.value === tool.adapter)?.label ?? tool.adapter}
                </Badge>
                {tool.handlerRef && (
                  <span className="text-muted-foreground max-w-[180px] truncate font-mono text-xs">
                    {tool.handlerRef}
                  </span>
                )}
                <Link
                  href={`/agents/tools/${tool.id}`}
                  className="text-muted-foreground hover:text-foreground shrink-0 text-xs underline-offset-2 hover:underline"
                >
                  Edit
                </Link>
                <button
                  type="button"
                  onClick={() => setToolToRemove(tool)}
                  disabled={deletingTool}
                  className="text-muted-foreground hover:text-destructive ml-1 shrink-0"
                  aria-label={`Remove ${tool.name}`}
                >
                  <TrashIcon className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      <ConfirmDialog
        open={confirmDeleteSkill}
        onOpenChange={setConfirmDeleteSkill}
        title={`Delete skill "${skill.name}"?`}
        description="This cannot be undone. Agents that reference this skill lose it on their next run, and its tool definitions go with it."
        confirmLabel="Delete skill"
        destructive
        onConfirm={deleteSkill}
      />

      <ConfirmDialog
        open={toolToRemove !== null}
        onOpenChange={(next) => {
          if (!next) setToolToRemove(null);
        }}
        title={toolToRemove ? `Remove tool "${toolToRemove.name}"?` : "Remove tool?"}
        description="The tool definition is deleted from this skill. Agents lose the capability on their next run."
        confirmLabel="Remove tool"
        destructive
        onConfirm={async () => {
          if (toolToRemove) await deleteTool(toolToRemove.id);
        }}
      />
    </PageShell>
  );
}
