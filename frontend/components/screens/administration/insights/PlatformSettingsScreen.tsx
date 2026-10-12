"use client";

import { AlertTriangleIcon, LockIcon, RotateCcwIcon } from "lucide-react";
import * as React from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { SettingsPage } from "@/components/settings/SettingsPage";
import type { SectionSelection } from "@/components/settings/use-settings-section";
import { AdministrationShell } from "@/components/screens/administration/organization/AdministrationShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Section } from "@/components/ui/section";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

import { humanizeKey } from "./feature-key";
import { PlatformSecretStores } from "./PlatformSecretStores";
import type { PlatformSecretStoresProps } from "./use-platform-secret-stores";
import type { PlatformSetting, usePlatformSettings } from "./use-platform-settings";

export type PlatformSettingsScreenProps = Omit<
  ReturnType<typeof usePlatformSettings>,
  "onRetry"
> & {
  onRetry: () => void;
  /** Which group is shown (`?section=`); without it every group renders. */
  section?: SectionSelection;
  /** The agent secret stores section; left out when absent. */
  secrets?: PlatformSecretStoresProps;
};

type Pending = { setting: PlatformSetting; value: string | null };

function slug(group: string) {
  return group.toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

function ValueEditor({
  setting,
  value,
  onChange,
  disabled,
}: {
  setting: PlatformSetting;
  value: string;
  onChange: (next: string) => void;
  disabled: boolean;
}) {
  const t = useTranslations("administration.platformSettings");
  const label = t("valueLabel", { key: setting.key });
  if (setting.kind === "bool" || setting.kind === "choice") {
    const options = setting.kind === "bool" ? ["true", "false"] : setting.choices;
    return (
      <Select value={value} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger aria-label={label} className="w-40">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option} value={option}>
              {setting.kind === "bool" ? t(option === "true" ? "on" : "off") : option}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }
  return (
    <Input
      aria-label={label}
      value={value}
      disabled={disabled}
      inputMode={setting.kind === "string" ? undefined : "decimal"}
      onChange={(event) => onChange(event.target.value)}
      className="w-56 max-w-full font-mono"
    />
  );
}

function SettingRow({
  setting,
  pending,
  locked,
  onSave,
  onReset,
}: {
  setting: PlatformSetting;
  pending: boolean;
  locked: boolean;
  onSave: (value: string) => void;
  onReset: () => void;
}) {
  const t = useTranslations("administration.platformSettings");
  const [draft, setDraft] = React.useState(setting.value);
  const dirty = draft !== setting.value;

  return (
    <div className="border-border/60 flex min-w-0 flex-col gap-2 border-b py-3 last:border-b-0">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <span className="min-w-0 font-medium [overflow-wrap:anywhere]">
          {humanizeKey(setting.key)}
        </span>
        <code className="text-muted-foreground bg-muted max-w-full rounded px-1.5 py-0.5 font-mono text-xs [overflow-wrap:anywhere]">
          {setting.key}
        </code>
        {setting.changed && <Badge variant="secondary">{t("changed")}</Badge>}
        {setting.warning && (
          <Badge variant="outline" className="gap-1">
            <AlertTriangleIcon className="size-3" /> {t("sensitive")}
          </Badge>
        )}
      </div>
      <p className="text-muted-foreground text-sm [overflow-wrap:anywhere]">{setting.helpText}</p>
      {setting.editable ? (
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <ValueEditor
            setting={setting}
            value={draft}
            onChange={setDraft}
            disabled={locked || pending}
          />
          <Button size="sm" onClick={() => onSave(draft)} disabled={!dirty || locked || pending}>
            {pending ? t("saving") : t("save")}
          </Button>
          {dirty && (
            <Button size="sm" variant="ghost" onClick={() => setDraft(setting.value)}>
              {t("discard")}
            </Button>
          )}
          {setting.changed && !dirty && (
            <Button size="sm" variant="ghost" onClick={onReset} disabled={locked || pending}>
              <RotateCcwIcon className="size-3.5" />{" "}
              {setting.default ? t("reset", { value: setting.default }) : t("resetEmpty")}
            </Button>
          )}
        </div>
      ) : (
        <p className="text-muted-foreground flex items-center gap-2 text-sm">
          <LockIcon className="size-3.5 shrink-0" />
          {setting.value && (
            <code className="bg-muted rounded px-1.5 py-0.5 font-mono text-xs">
              {setting.value}
            </code>
          )}
          <span>{setting.readOnlyReason}</span>
        </p>
      )}
    </div>
  );
}

/** Install settings grouped as in the Django admin; each saves on its own (#1861). */
export function PlatformSettingsScreen({
  settings,
  loading,
  error,
  onRetry,
  pendingKey,
  save,
  reset,
  section,
  secrets,
}: PlatformSettingsScreenProps) {
  const t = useTranslations("administration.platformSettings");
  const [confirm, setConfirm] = React.useState<Pending | null>(null);

  const groups = React.useMemo(() => {
    const out = new Map<string, PlatformSetting[]>();
    for (const setting of settings)
      out.set(setting.group, [...(out.get(setting.group) ?? []), setting]);
    return [...out.entries()];
  }, [settings]);

  const commit = (setting: PlatformSetting, value: string | null) =>
    value === null ? reset(setting) : save(setting, value);

  const request = (setting: PlatformSetting, value: string | null) => {
    if (setting.warning) {
      setConfirm({ setting, value });
      return;
    }
    commit(setting, value).catch((cause: Error) => toast.error(cause.message));
  };

  return (
    <AdministrationShell
      fnKey="platform-settings"
      title={t("title")}
      description={t("description")}
    >
      {error && settings.length === 0 ? (
        <EmptyState
          icon={<AlertTriangleIcon className="text-danger size-5" />}
          title={t("loadError")}
          description={error.message}
          secondary={
            <Button size="sm" variant="outline" onClick={onRetry}>
              {t("retry")}
            </Button>
          }
        />
      ) : loading && settings.length === 0 ? (
        <div className="flex flex-col gap-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : (
        <SettingsPage
          single={section}
          sections={[
            ...groups.map(([group, rows]) => ({
              id: slug(group),
              title: group,
              content: (
                <Section title={group}>
                  <Card>
                    <CardContent className="flex min-w-0 flex-col">
                      {rows.map((setting) => (
                        <SettingRow
                          // Keyed by value too: a saved value starts a fresh draft.
                          key={`${setting.key}:${setting.value}`}
                          setting={setting}
                          pending={pendingKey === setting.key}
                          locked={pendingKey !== null && pendingKey !== setting.key}
                          onSave={(value) => request(setting, value)}
                          onReset={() => request(setting, null)}
                        />
                      ))}
                    </CardContent>
                  </Card>
                </Section>
              ),
            })),
            ...(secrets
              ? [
                  {
                    id: "agent-secret-stores",
                    title: t("secrets.title"),
                    content: (
                      <Section title={t("secrets.title")}>
                        <PlatformSecretStores {...secrets} />
                      </Section>
                    ),
                  },
                ]
              : []),
          ]}
        />
      )}

      <ConfirmDialog
        open={confirm !== null}
        onOpenChange={(next) => {
          if (!next) setConfirm(null);
        }}
        title={t("confirmTitle", { key: confirm ? humanizeKey(confirm.setting.key) : "" })}
        description={confirm?.setting.warning}
        confirmLabel={t("confirm")}
        destructive
        onConfirm={async () => {
          if (confirm) await commit(confirm.setting, confirm.value);
        }}
      />
    </AdministrationShell>
  );
}
