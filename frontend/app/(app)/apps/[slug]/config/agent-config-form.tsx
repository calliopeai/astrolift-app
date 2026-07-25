"use client";

/**
 * Schema-driven visual editor for an agent config repo's astrolift.toml (#1172).
 *
 * Sibling to `manifest-form.tsx` (the app manifest builder): same controlled
 * `onChange`-bubbles-a-new-model discipline, same collapsible list→detail
 * sub-editors, same field primitives — but for the agent library schema
 * (`astrolift_version` + `[skills.<slug>]` / `[tools.<slug>]` + `[environment]`).
 * The parent owns the TOML round-trip; this is a pure controlled view over
 * `AgentConfigModel`.
 */

import {
  ChevronDownIcon,
  ChevronRightIcon,
  PlusIcon,
  SparklesIcon,
  TrashIcon,
  WrenchIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Section } from "@/components/ui/section";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import {
  type AgentConfigModel,
  type AgentSkillModel,
  type AgentToolModel,
  emptySkill,
  emptyTool,
} from "@/lib/manifest/agent-config-model";
import {
  TOOL_ADAPTERS,
  errorFor,
  hasErrorPrefix,
  type ManifestErr,
} from "@/lib/manifest/agent-config-schema";

import {
  FieldRow,
  KeyValueEditor,
  ListField,
  SelectField,
  TextField,
  ToggleField,
} from "./manifest-fields";

type Props = {
  model: AgentConfigModel;
  errors: ManifestErr[];
  onChange: (model: AgentConfigModel) => void;
};

/** Multi-line text field — used for skill content and raw JSON schema fields. */
function TextareaField({
  label,
  value,
  onChange,
  placeholder,
  help,
  error,
  rows = 4,
  mono,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  help?: string;
  error?: string;
  rows?: number;
  mono?: boolean;
}) {
  return (
    <FieldRow label={label} help={help} error={error}>
      <Textarea
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        rows={rows}
        spellCheck={false}
        className={mono ? "font-mono text-xs" : "text-xs"}
        aria-invalid={!!error}
      />
    </FieldRow>
  );
}

/** Collapsible list entry — mirrors the app builder's CollapsibleEntry shell. */
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

// ─── Skill sub-editor ────────────────────────────────────────────────────────

function SkillEditor({
  skill,
  index,
  errors,
  onChange,
  onRemove,
}: {
  skill: AgentSkillModel;
  index: number;
  errors: ManifestErr[];
  onChange: (s: AgentSkillModel) => void;
  onRemove: () => void;
}) {
  const t = useTranslations("apps.config.builder");
  const [open, setOpen] = React.useState(true);
  const path = `skills[${index}]`;
  const patch = (p: Partial<AgentSkillModel>) => onChange({ ...skill, ...p });

  return (
    <CollapsibleEntry
      open={open}
      onOpenChange={setOpen}
      icon={<SparklesIcon className="size-4" />}
      title={skill.slug}
      subtitle={skill.name || undefined}
      invalid={hasErrorPrefix(errors, path)}
      onRemove={onRemove}
      removeLabel={t("removeSkill")}
      badges={
        !skill.is_active ? (
          <Badge variant="outline" className="text-2xs">
            {t("inactive")}
          </Badge>
        ) : undefined
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label={t("slug")}
          value={skill.slug}
          mono
          error={errorFor(errors, `${path}.slug`)}
          onChange={(v) => patch({ slug: v })}
        />
        <TextField label={t("skillName")} value={skill.name} onChange={(v) => patch({ name: v })} />
      </div>
      <TextField
        label={t("description")}
        value={skill.description}
        onChange={(v) => patch({ description: v })}
      />
      <TextareaField
        label={t("content")}
        value={skill.content}
        rows={6}
        help={t("contentHelp")}
        onChange={(v) => patch({ content: v })}
      />
      <div className="grid gap-3 sm:grid-cols-2">
        <ListField
          label={t("dependencies")}
          value={skill.dependencies}
          placeholder={t("commandHint")}
          onChange={(v) => patch({ dependencies: v })}
        />
        <ListField
          label={t("scaffoldingTags")}
          value={skill.scaffolding_tags}
          placeholder={t("commandHint")}
          onChange={(v) => patch({ scaffolding_tags: v })}
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label={t("agentType")}
          value={skill.agent_type}
          mono
          onChange={(v) => patch({ agent_type: v })}
        />
        <ToggleField
          label={t("isActive")}
          value={skill.is_active}
          onChange={(v) => patch({ is_active: v })}
        />
      </div>
    </CollapsibleEntry>
  );
}

// ─── Tool sub-editor ─────────────────────────────────────────────────────────

function ToolEditor({
  tool,
  index,
  errors,
  onChange,
  onRemove,
}: {
  tool: AgentToolModel;
  index: number;
  errors: ManifestErr[];
  onChange: (t: AgentToolModel) => void;
  onRemove: () => void;
}) {
  const t = useTranslations("apps.config.builder");
  const path = `tools[${index}]`;
  const [open, setOpen] = React.useState(true);
  const patch = (p: Partial<AgentToolModel>) => onChange({ ...tool, ...p });

  return (
    <CollapsibleEntry
      open={open}
      onOpenChange={setOpen}
      icon={<WrenchIcon className="size-4" />}
      title={tool.slug}
      subtitle={tool.name || undefined}
      invalid={hasErrorPrefix(errors, path)}
      onRemove={onRemove}
      removeLabel={t("removeTool")}
      badges={
        <>
          {tool.adapter && (
            <Badge variant="outline" className="text-2xs">
              {tool.adapter}
            </Badge>
          )}
          {tool.is_builtin && (
            <Badge variant="secondary" className="text-2xs">
              {t("builtin")}
            </Badge>
          )}
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label={t("slug")}
          value={tool.slug}
          mono
          error={errorFor(errors, `${path}.slug`)}
          onChange={(v) => patch({ slug: v })}
        />
        <TextField label={t("toolName")} value={tool.name} onChange={(v) => patch({ name: v })} />
      </div>
      <TextField
        label={t("description")}
        value={tool.description}
        onChange={(v) => patch({ description: v })}
      />
      <div className="grid gap-3 sm:grid-cols-2">
        <SelectField
          label={t("adapter")}
          value={tool.adapter}
          options={TOOL_ADAPTERS}
          onChange={(v) => patch({ adapter: v })}
        />
        <TextField
          label={t("handlerRef")}
          value={tool.handler_ref}
          mono
          placeholder="module.path:function"
          onChange={(v) => patch({ handler_ref: v })}
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label={t("capabilityGroup")}
          value={tool.capability_group}
          mono
          onChange={(v) => patch({ capability_group: v })}
        />
        <ToggleField
          label={t("isBuiltin")}
          value={tool.is_builtin}
          onChange={(v) => patch({ is_builtin: v })}
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <ListField
          label={t("commands")}
          value={tool.commands}
          placeholder={t("commandHint")}
          onChange={(v) => patch({ commands: v })}
        />
        <ListField
          label={t("requiredPackages")}
          value={tool.required_packages}
          placeholder={t("commandHint")}
          onChange={(v) => patch({ required_packages: v })}
        />
      </div>
      <ListField
        label={t("agentTypeBindings")}
        value={tool.agent_type_bindings}
        placeholder={t("commandHint")}
        onChange={(v) => patch({ agent_type_bindings: v })}
      />
      <div className="grid gap-3 sm:grid-cols-2">
        <TextareaField
          label={t("inputSchema")}
          value={tool.input_schema}
          rows={5}
          mono
          help={t("jsonHelp")}
          placeholder='{ "type": "object" }'
          error={errorFor(errors, `${path}.input_schema`)}
          onChange={(v) => patch({ input_schema: v })}
        />
        <TextareaField
          label={t("outputSchema")}
          value={tool.output_schema}
          rows={5}
          mono
          help={t("jsonHelp")}
          placeholder='{ "type": "object" }'
          error={errorFor(errors, `${path}.output_schema`)}
          onChange={(v) => patch({ output_schema: v })}
        />
      </div>
      <TextareaField
        label={t("implementationConfig")}
        value={tool.implementation_config}
        rows={4}
        mono
        help={t("jsonHelp")}
        placeholder="{ }"
        error={errorFor(errors, `${path}.implementation_config`)}
        onChange={(v) => patch({ implementation_config: v })}
      />
    </CollapsibleEntry>
  );
}

// ─── Root form ───────────────────────────────────────────────────────────────

export function AgentConfigFormBuilder({ model, errors, onChange }: Props) {
  const t = useTranslations("apps.config.builder");
  const patch = (p: Partial<AgentConfigModel>) => onChange({ ...model, ...p });
  const patchEnv = (p: Partial<AgentConfigModel["environment"]>) =>
    onChange({ ...model, environment: { ...model.environment, ...p } });
  const preservedKeys = Object.keys(model.extra);

  return (
    <div className="flex flex-col gap-6">
      <Section title={t("agentConfigSection")} description={t("agentConfigHint")}>
        <div className="max-w-md">
          <TextField
            label={t("astroliftVersion")}
            value={model.astrolift_version}
            mono
            error={errorFor(errors, "astrolift_version")}
            onChange={(v) => patch({ astrolift_version: v })}
          />
        </div>
      </Section>

      <Section title={t("environment")} description={t("agentEnvHint")}>
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField
            label={t("toolPreset")}
            value={model.environment.tool_preset}
            mono
            help={t("toolPresetHelp")}
            onChange={(v) => patchEnv({ tool_preset: v })}
          />
          <ToggleField
            label={t("allowInstall")}
            value={model.environment.allow_install}
            help={t("allowInstallHelp")}
            onChange={(v) => patchEnv({ allow_install: v })}
          />
        </div>
        <div className="mt-3 flex flex-col gap-2">
          <span className="text-xs font-medium">{t("envVars")}</span>
          <KeyValueEditor
            entries={model.environment.vars}
            onChange={(vars) => patchEnv({ vars })}
            addLabel={t("addEnv")}
          />
        </div>
      </Section>

      <Section
        title={t("skills")}
        description={t("skillsHint")}
        action={
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => patch({ skills: [...model.skills, emptySkill(`skill-${model.skills.length + 1}`)] })}
          >
            <PlusIcon className="size-3.5" /> {t("addSkill")}
          </Button>
        }
      >
        {model.skills.length === 0 ? (
          <EmptyState
            icon={<SparklesIcon className="size-5" />}
            title={t("noSkills")}
            description={t("noSkillsHint")}
          />
        ) : (
          <div className="flex flex-col gap-3">
            {model.skills.map((s, i) => (
              <SkillEditor
                key={i}
                skill={s}
                index={i}
                errors={errors}
                onChange={(next) => patch({ skills: model.skills.map((x, j) => (j === i ? next : x)) })}
                onRemove={() => patch({ skills: model.skills.filter((_, j) => j !== i) })}
              />
            ))}
          </div>
        )}
      </Section>

      <Section
        title={t("tools")}
        description={t("toolsHint")}
        action={
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => patch({ tools: [...model.tools, emptyTool(`tool-${model.tools.length + 1}`)] })}
          >
            <PlusIcon className="size-3.5" /> {t("addTool")}
          </Button>
        }
      >
        {model.tools.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t("noTools")}</p>
        ) : (
          <div className="flex flex-col gap-3">
            {model.tools.map((tool, i) => (
              <ToolEditor
                key={i}
                tool={tool}
                index={i}
                errors={errors}
                onChange={(next) => patch({ tools: model.tools.map((x, j) => (j === i ? next : x)) })}
                onRemove={() => patch({ tools: model.tools.filter((_, j) => j !== i) })}
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
