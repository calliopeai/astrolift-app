"use client";

import { Loader2Icon, PlusIcon } from "lucide-react";
import { useTranslations } from "next-intl";
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

import { ENVIRONMENT_NAME, type useAddEnvironment } from "./use-add-environment";

export type AddEnvironmentViewProps = ReturnType<typeof useAddEnvironment>;

// Never a cluster slug; a Select item needs a non-empty value.
const DEFAULT_CLUSTER = "*";

/**
 * Add an environment to an app after registration (#2398): a name and,
 * optionally, a cluster other than the app's default. The environment is
 * placed the way registration places one; its first deploy brings it up.
 */
export function AddEnvironmentView({ clusters, creating, onCreate }: AddEnvironmentViewProps) {
  const t = useTranslations("apps.settings.addEnvironment");
  const [name, setName] = React.useState("");
  const [cluster, setCluster] = React.useState(DEFAULT_CLUSTER);
  const valid = ENVIRONMENT_NAME.test(name.trim());
  const clusterName = clusters.find((c) => c.slug === cluster)?.name;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!valid || creating) return;
    if (await onCreate(name.trim(), cluster === DEFAULT_CLUSTER ? null : cluster)) {
      setName("");
      setCluster(DEFAULT_CLUSTER);
    }
  }

  return (
    <Section title={t("title")} description={t("description")} headingLevel="h3">
      <form className="flex min-w-0 flex-wrap items-end gap-3" onSubmit={(e) => void submit(e)}>
        <div className="flex min-w-0 flex-col gap-1.5">
          <Label htmlFor="add-environment-name">{t("nameLabel")}</Label>
          <Input
            id="add-environment-name"
            className="w-56 font-mono"
            value={name}
            onChange={(e) => setName(e.target.value)}
            // An environment name, the same in every language.
            placeholder="staging"
            autoComplete="off"
            spellCheck={false}
            aria-invalid={name !== "" && !valid}
          />
        </div>
        {clusters.length ? (
          <div className="flex min-w-0 flex-col gap-1.5">
            <Label>{t("clusterLabel")}</Label>
            <Select value={cluster} onValueChange={(v) => setCluster(v ?? DEFAULT_CLUSTER)}>
              <SelectTrigger className="w-56" aria-label={t("clusterLabel")}>
                <SelectValue>{clusterName ?? t("defaultCluster")}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={DEFAULT_CLUSTER}>{t("defaultCluster")}</SelectItem>
                {clusters.map((c) => (
                  <SelectItem key={c.slug} value={c.slug}>
                    {c.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ) : null}
        <Can permission="app.update">
          <Button type="submit" disabled={!valid || creating}>
            {creating ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <PlusIcon className="size-4" />
            )}
            {t("button")}
          </Button>
        </Can>
      </form>
      <p className="text-muted-foreground text-2xs mt-2">{t("nameHint")}</p>
    </Section>
  );
}
