"use client";

import { KeyIcon, Loader2Icon, PlusIcon, Trash2Icon } from "lucide-react";
import * as React from "react";

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

const SUGGESTED_OVERRIDE_KEYS: { key: string; hint: string }[] = [
  { key: "replicas", hint: "Workload replica count" },
  { key: "cpu_request", hint: "Per-pod CPU request" },
  { key: "cpu_limit", hint: "Per-pod CPU limit" },
  { key: "memory_request", hint: "Per-pod memory request" },
  { key: "memory_limit", hint: "Per-pod memory limit" },
];

/**
 * Per-environment key/value overrides that supplement the app's base
 * configuration. Hidden when the app has no environments.
 */
export function EnvironmentSettingsView({
  envs,
  adding,
  onAdd,
  onClear,
}: EnvironmentSettingsViewProps) {
  const [selectedEnvId, setSelectedEnvId] = React.useState<string>("");
  const [newKey, setNewKey] = React.useState("");
  const [newValue, setNewValue] = React.useState("");

  if (envs.length === 0) return null;

  const effectiveEnv = envs.find((e) => e.id === (selectedEnvId || envs[0]?.id));
  const settings = effectiveEnv?.settings ?? [];

  async function handleAdd() {
    if (!effectiveEnv) return;
    if (await onAdd(effectiveEnv.id, newKey, newValue)) {
      setNewKey("");
      setNewValue("");
    }
  }

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <KeyIcon className="text-muted-foreground size-4 shrink-0" />
          Environment overrides
        </span>
      }
      description="Per-environment key/value settings that supplement the app's base configuration."
    >
      {envs.length > 1 && (
        <div className="flex items-center gap-2">
          <Label className="text-muted-foreground text-xs">Environment</Label>
          <Select value={selectedEnvId || envs[0]?.id} onValueChange={setSelectedEnvId}>
            <SelectTrigger className="w-48">
              <SelectValue placeholder="Select environment" />
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

      {settings.length === 0 ? (
        <p className="text-muted-foreground text-xs italic">
          No overrides set for{" "}
          <span className="text-foreground font-mono">{effectiveEnv?.name ?? ""}</span>. Add one
          below or pick a suggested key.
        </p>
      ) : (
        <div className="flex flex-col gap-1">
          {settings.map((s) => (
            <div key={s.id} className="hover:bg-muted/40 flex items-center gap-2 rounded px-2 py-1">
              <span className="w-40 shrink-0 font-mono text-xs">{s.key}</span>
              <span className="text-muted-foreground flex-1 truncate font-mono text-xs">
                {s.value || "(empty)"}
              </span>
              <Can permission="app.update">
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-destructive hover:text-destructive h-6 px-2 text-xs"
                  onClick={() => effectiveEnv && void onClear(effectiveEnv.id, s.key)}
                  title="Clear override"
                >
                  <Trash2Icon className="size-3" />
                </Button>
              </Can>
            </div>
          ))}
        </div>
      )}

      <Can permission="app.update">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-muted-foreground text-2xs mr-1">Suggested:</span>
            {SUGGESTED_OVERRIDE_KEYS.map((s) => {
              const alreadySet = settings.some((row) => row.key === s.key);
              return (
                <Tooltip key={s.key}>
                  <TooltipTrigger asChild>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="text-2xs h-6 px-2 font-mono"
                      disabled={alreadySet}
                      onClick={() => setNewKey(s.key)}
                    >
                      {s.key}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p className="text-xs">{alreadySet ? `${s.hint} (already set)` : s.hint}</p>
                  </TooltipContent>
                </Tooltip>
              );
            })}
          </div>
          <div className="flex items-center gap-2">
            <Input
              list="env-override-suggested-keys"
              placeholder="KEY"
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              className="w-44 font-mono text-xs"
            />
            <datalist id="env-override-suggested-keys">
              {SUGGESTED_OVERRIDE_KEYS.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.hint}
                </option>
              ))}
            </datalist>
            <Input
              placeholder="value"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              className="flex-1 font-mono text-xs"
            />
            <Button size="sm" variant="outline" disabled={adding} onClick={() => void handleAdd()}>
              {adding ? (
                <Loader2Icon className="size-3.5 animate-spin" />
              ) : (
                <PlusIcon className="size-3.5" />
              )}
              Add override
            </Button>
          </div>
        </div>
      </Can>
    </Section>
  );
}
