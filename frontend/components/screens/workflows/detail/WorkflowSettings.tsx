"use client";

import * as React from "react";
import { useTranslations } from "next-intl";

import {
  DangerAction,
  type ReadOnlyReason,
  SettingsPage,
  SettingsSection,
} from "@/components/settings/SettingsPage";
import type { SectionSelection } from "@/components/settings/use-settings-section";
import {
  DeliveryChannelPicker,
  type DeliveryRecord,
} from "@/components/screens/workflows/delivery/DeliveryChannelPicker";
import type { DeliveryChannel } from "@/components/screens/workflows/delivery/DeliveryChannelsPanel";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export interface WorkflowSettingsGeneral {
  name: string;
  slug: string;
  description: string;
}

/** Where a definition was declared; a configured workflow has none. */
export interface WorkflowSettingsSource {
  repo: string;
  path: string;
  ref: string;
}

/** Where a configured workflow's scheduled results go (#2244). */
export interface WorkflowDeliverySettings {
  channels: DeliveryChannel[];
  selected: string[];
  recent: DeliveryRecord[];
  saving?: boolean;
  error?: string | null;
  /** Replaces the selection. Resolves true on success. */
  onSave?: (channelGuids: string[]) => Promise<boolean>;
}

export interface WorkflowSettingsViewProps {
  kind: "configured" | "definition";
  general: WorkflowSettingsGeneral;
  source?: WorkflowSettingsSource | null;
  /** A configured workflow the viewer may not change: `workflow.update`. */
  readOnly?: ReadOnlyReason;
  single: SectionSelection;
  saving?: boolean;
  saveError?: string | null;
  /** Saves name and description; a configured workflow only. Resolves true on success. */
  onSave?: (next: { name: string; description: string }) => Promise<boolean>;
  /** Present when the viewer may delete it; the Danger zone is left out otherwise. */
  onDelete?: () => Promise<unknown>;
  /** A configured workflow's delivery channels; the section is left out without it. */
  delivery?: WorkflowDeliverySettings;
}

function Field({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
    </div>
  );
}

function ReadField({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="mt-1 font-mono text-xs [overflow-wrap:anywhere]">{value}</dd>
    </div>
  );
}

function DeliverySection({ delivery }: { delivery: WorkflowDeliverySettings }) {
  const t = useTranslations("workflowDelivery");
  const [value, setValue] = React.useState(delivery.selected);
  const dirty =
    value.length !== delivery.selected.length || value.some((g) => !delivery.selected.includes(g));
  return (
    <SettingsSection
      title={t("sectionTitle")}
      description={t("sectionDescription")}
      dirty={dirty}
      saving={delivery.saving}
      error={delivery.error}
      onSave={delivery.onSave ? () => delivery.onSave?.(value) : undefined}
      onCancel={() => setValue(delivery.selected)}
    >
      <DeliveryChannelPicker
        channels={delivery.channels}
        value={value}
        onChange={setValue}
        recent={delivery.recent}
      />
    </SettingsSection>
  );
}

/**
 * The Settings tab (spec 44 §5.2, §5.3): General and one Danger zone, one
 * section at a time by `?section=`. A configured workflow's name and
 * description save on their own; a definition's General is where it was
 * declared (its stages are edited in the Builder). Delete sits in the Danger
 * zone behind a ConfirmDialog. Pure: the hook holds the mutations.
 */
export function WorkflowSettingsView({
  kind,
  general,
  source,
  readOnly,
  single,
  saving = false,
  saveError,
  onSave,
  onDelete,
  delivery,
}: WorkflowSettingsViewProps) {
  const t = useTranslations("workflowSettings");
  const tDelivery = useTranslations("workflowDelivery");
  const [name, setName] = React.useState(general.name);
  const [description, setDescription] = React.useState(general.description);
  const dirty = name !== general.name || description !== general.description;
  const reset = () => {
    setName(general.name);
    setDescription(general.description);
  };

  const generalSection =
    kind === "configured" ? (
      <SettingsSection
        title={t("general")}
        description={t("generalDescription")}
        dirty={dirty && name.trim() !== ""}
        saving={saving}
        error={saveError}
        onSave={onSave ? () => onSave({ name: name.trim(), description }) : undefined}
        onCancel={reset}
      >
        <Field id="workflow-name" label={t("name")}>
          <Input
            id="workflow-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </Field>
        <Field id="workflow-description" label={t("description")}>
          <Textarea
            id="workflow-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
          />
        </Field>
        <dl>
          <ReadField label={t("slug")} value={general.slug} />
        </dl>
      </SettingsSection>
    ) : (
      <SettingsSection
        title={t("general")}
        description={source?.repo ? t("repoDescription") : t("definitionDescription")}
      >
        <dl className="grid min-w-0 gap-3 sm:grid-cols-2">
          <ReadField label={t("name")} value={general.name} />
          <ReadField label={t("slug")} value={general.slug} />
          {general.description && (
            <ReadField label={t("description")} value={general.description} />
          )}
          {source?.repo && <ReadField label={t("repository")} value={source.repo} />}
          {source?.repo && <ReadField label={t("manifest")} value={source.path || t("builtIn")} />}
          {source?.repo && <ReadField label={t("reconciledRef")} value={source.ref || t("none")} />}
        </dl>
      </SettingsSection>
    );

  return (
    <SettingsPage
      single={single}
      readOnly={readOnly}
      sections={[
        { id: "general", title: t("general"), content: generalSection },
        ...(kind === "configured" && delivery
          ? [
              {
                id: "delivery",
                title: tDelivery("sectionTitle"),
                // Keyed on the saved selection, so a save or refetch resets it.
                content: <DeliverySection key={delivery.selected.join(",")} delivery={delivery} />,
              },
            ]
          : []),
      ]}
      dangerZone={
        onDelete ? (
          <DangerAction
            title={kind === "configured" ? t("deleteWorkflowTitle") : t("deleteDefinitionTitle")}
            description={
              kind === "configured"
                ? t("deleteWorkflowDescription")
                : t("deleteDefinitionDescription")
            }
            actionLabel={kind === "configured" ? t("deleteWorkflow") : t("deleteDefinition")}
            confirmTitle={t("confirmTitle", { name: general.name })}
            confirmDescription={
              kind === "configured" ? t("confirmWorkflowDescription") : t("cannotBeUndone")
            }
            onConfirm={() => onDelete()}
          />
        ) : undefined
      }
    />
  );
}
