"use client";

import { KeyIcon, Loader2Icon, PlusIcon, Trash2Icon } from "lucide-react";
import * as React from "react";
import { useTranslations } from "next-intl";

import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { Can } from "@/components/Can";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

import type { useEnvironmentSettings } from "./use-environment-settings";

export type EnvironmentSettingsViewProps = ReturnType<typeof useEnvironmentSettings>;

const SUGGESTED_OVERRIDE_KEYS = [
  "replicas",
  "cpu_request",
  "cpu_limit",
  "memory_request",
  "memory_limit",
] as const;

/**
 * Per-environment key/value overrides that supplement the app's base
 * configuration. Hidden when the app has no environments.
 */
export function EnvironmentSettingsView({
  envs,
  adding,
  clearing,
  onAdd,
  onClear,
  target = "",
  loading = false,
  error = null,
  onRetry,
}: EnvironmentSettingsViewProps) {
  const t = useTranslations("apps.environmentOverrides");
  const perms = useMyPermissions();
  const allowed = (perms.loading && perms.granted.size === 0) || perms.can("app.update");
  const [selectedEnvId, setSelectedEnvId] = React.useState<string>("");
  const effectiveEnv = envs.find((e) => e.id === selectedEnvId) ?? envs[0];
  const settings = effectiveEnv?.settings ?? [];
  // Local observations prevent A→B→A from reviving a draft; the server has no CAS.
  const fingerprint = JSON.stringify([
    target,
    effectiveEnv?.id,
    effectiveEnv?.registeredAppSlug,
    effectiveEnv?.clusterId,
    effectiveEnv?.clusterSlug,
    effectiveEnv?.clusterProviderPluginSlug,
    effectiveEnv?.createdAt,
    allowed,
  ]);
  const [lease, setLease] = React.useState({ fingerprint, serial: 0 });
  if (lease.fingerprint !== fingerprint) setLease({ fingerprint, serial: lease.serial + 1 });
  const serial = lease.serial;
  const currentSerial = React.useRef<number | null>(serial);
  React.useLayoutEffect(() => {
    currentSerial.current = serial;
    return () => {
      currentSerial.current = null;
    };
  }, [serial]);
  const [draft, setDraft] = React.useState({ serial, revision: 0, key: "", value: "" });
  const latestDraft = React.useRef(draft);
  React.useLayoutEffect(() => {
    latestDraft.current = draft;
  }, [draft]);
  const currentDraft =
    draft.serial === serial ? draft : { serial, revision: draft.revision, key: "", value: "" };
  const newKey = currentDraft.key;
  const newValue = currentDraft.value;
  const writable = allowed && !error && Boolean(effectiveEnv);
  function edit(field: "key" | "value", value: string) {
    const next = { ...currentDraft, [field]: value, revision: latestDraft.current.revision + 1 };
    latestDraft.current = next;
    setDraft(next);
  }
  async function handleAdd() {
    if (!effectiveEnv || !writable || currentSerial.current !== serial) return;
    const reviewed = latestDraft.current;
    if (await onAdd(effectiveEnv.id, newKey, newValue)) {
      if (currentSerial.current === serial && latestDraft.current.revision === reviewed.revision) {
        const next = { serial, revision: reviewed.revision + 1, key: "", value: "" };
        latestDraft.current = next;
        setDraft(next);
      }
    }
  }
  if (!loading && !error && envs.length === 0) return null;

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <KeyIcon className="text-muted-foreground size-4 shrink-0" />
          {t("title")}
        </span>
      }
      description={t("description")}
    >
      {loading && (
        <p role="status" className="text-muted-foreground text-xs">
          {t("loading")}
        </p>
      )}
      {error && (
        <div role="alert" className="space-y-2">
          <p>{t("readFailed")}</p>
          {error !== t("readFailed") && (
            <p className="font-mono text-xs [overflow-wrap:anywhere]">{error}</p>
          )}
          {onRetry && (
            <Button type="button" size="sm" variant="outline" onClick={onRetry}>
              {t("retry")}
            </Button>
          )}
        </div>
      )}
      {envs.length > 1 && (
        <div className="flex items-center gap-2">
          <Label className="text-muted-foreground text-xs">{t("environment")}</Label>
          <Select value={effectiveEnv?.id} onValueChange={(id) => setSelectedEnvId(id ?? "")}>
            <SelectTrigger className="w-48" aria-label={t("environment")}>
              <SelectValue placeholder={t("selectEnvironment")} />
            </SelectTrigger>
            <SelectContent>
              {envs.map((e) => (
                <SelectItem key={e.id} value={e.id}>
                  {e.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {effectiveEnv &&
        (settings.length === 0 ? (
          <p className="text-muted-foreground text-xs italic">
            {t.rich("empty", {
              environment: effectiveEnv?.name ?? "",
              name: (chunks) => <span className="text-foreground font-mono">{chunks}</span>,
            })}
          </p>
        ) : (
          <div className="flex flex-col gap-1">
            {settings.map((s) => (
              <div
                key={s.id}
                className="hover:bg-muted/40 flex items-center gap-2 rounded px-2 py-1"
              >
                <span className="w-40 shrink-0 font-mono text-xs">{s.key}</span>
                <span className="text-muted-foreground flex-1 truncate font-mono text-xs">
                  {s.value || t("emptyValue")}
                </span>
                <Can permission="app.update">
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-destructive hover:text-destructive h-6 px-2 text-xs"
                    onClick={() =>
                      writable &&
                      currentSerial.current === serial &&
                      effectiveEnv &&
                      void onClear(effectiveEnv.id, s.key)
                    }
                    title={t("clearTitle")}
                    aria-label={t("clearLabel", { key: s.key })}
                    disabled={!writable || clearing.has(JSON.stringify([effectiveEnv?.id, s.key]))}
                  >
                    {clearing.has(JSON.stringify([effectiveEnv?.id, s.key])) ? (
                      <Loader2Icon className="size-3 animate-spin" />
                    ) : (
                      <Trash2Icon className="size-3" />
                    )}
                  </Button>
                </Can>
              </div>
            ))}
          </div>
        ))}

      <Can permission="app.update">
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            void handleAdd();
          }}
        >
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-muted-foreground text-2xs mr-1">{t("suggested")}</span>
            {SUGGESTED_OVERRIDE_KEYS.map((key) => {
              const alreadySet = settings.some((row) => row.key === key);
              return (
                <Tooltip key={key}>
                  <TooltipTrigger asChild>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="text-2xs h-6 px-2 font-mono"
                      disabled={!writable || alreadySet}
                      onClick={() => edit("key", key)}
                    >
                      {key}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p className="text-xs">
                      {alreadySet
                        ? t("alreadySet", { hint: t(`hints.${key}`) })
                        : t(`hints.${key}`)}
                    </p>
                  </TooltipContent>
                </Tooltip>
              );
            })}
          </div>
          <div className="flex items-center gap-2">
            <Input
              list="env-override-suggested-keys"
              placeholder={t("keyPlaceholder")}
              aria-label={t("keyLabel")}
              disabled={!writable}
              value={newKey}
              onChange={(e) => edit("key", e.target.value)}
              className="w-44 font-mono text-xs"
            />
            <datalist id="env-override-suggested-keys">
              {SUGGESTED_OVERRIDE_KEYS.map((key) => (
                <option key={key} value={key}>
                  {t(`hints.${key}`)}
                </option>
              ))}
            </datalist>
            <Input
              placeholder={t("valuePlaceholder")}
              aria-label={t("valueLabel")}
              disabled={!writable}
              value={newValue}
              onChange={(e) => edit("value", e.target.value)}
              className="flex-1 font-mono text-xs"
            />
            <Button type="submit" size="sm" variant="outline" disabled={!writable || adding}>
              {adding ? (
                <Loader2Icon className="size-3.5 animate-spin" />
              ) : (
                <PlusIcon className="size-3.5" />
              )}
              {t("add")}
            </Button>
          </div>
        </form>
      </Can>
    </Section>
  );
}
