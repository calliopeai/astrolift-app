"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { START_DEPLOYMENT } from "@/graphql/lifecycle/lifecycle.mutations";
import {
  LIST_DEPLOYMENTS,
  LIST_ENVIRONMENTS,
} from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppEnvironment,
  AstroliftDeployment,
} from "@/graphql/lifecycle/lifecycle.types";
import { LIST_APPS } from "@/graphql/registry/registry.queries";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface AppListItem {
  id: string;
  slug: string;
  name: string;
}

interface AppsResp {
  astroliftApps: AppListItem[];
}

interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

interface MutationResp {
  startDeployment: {
    ok: boolean;
    errors: { code: string; message: string; field?: string | null }[];
    data: AstroliftDeployment | null;
  };
}

const TRIGGER_KINDS = ["manual", "ci", "scheduled", "promotion"] as const;

export function StartDeploymentDialog({ open, onOpenChange }: Props) {
  const t = useTranslations("lists.deployments.startSheet");
  const apps = useQuery<AppsResp>(LIST_APPS, { skip: !open });
  const [appSlug, setAppSlug] = React.useState("");
  const envs = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    skip: !open || !appSlug,
  });
  const [environmentName, setEnvironmentName] = React.useState("");
  const [imageTag, setImageTag] = React.useState("");
  const [imageDigest, setImageDigest] = React.useState("");
  const [triggerKind, setTriggerKind] =
    React.useState<(typeof TRIGGER_KINDS)[number]>("manual");

  React.useEffect(() => {
    if (!open) {
      setAppSlug("");
      setEnvironmentName("");
      setImageTag("");
      setImageDigest("");
      setTriggerKind("manual");
    }
  }, [open]);

  React.useEffect(() => {
    setEnvironmentName("");
  }, [appSlug]);

  const [start, { loading }] = useMutation<MutationResp>(START_DEPLOYMENT, {
    refetchQueries: [{ query: LIST_DEPLOYMENTS, variables: { limit: 100 } }],
    awaitRefetchQueries: true,
  });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!appSlug || !environmentName || !imageTag) return;
    const { data } = await start({
      variables: {
        input: {
          appSlug,
          environmentName,
          imageTag,
          imageDigest: imageDigest.trim() || null,
          triggerKind,
        },
      },
    });
    if (data?.startDeployment.ok) {
      toast.success(
        `Deployment started: ${data.startDeployment.data?.status}`,
      );
      onOpenChange(false);
    } else {
      toast.error(data?.startDeployment.errors[0]?.message ?? "Failed");
    }
  }

  const appList = apps.data?.astroliftApps ?? [];
  const envList = envs.data?.astroliftEnvironments ?? [];

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>
        <form
          onSubmit={submit}
          className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4"
        >
          <div className="space-y-2">
            <Label htmlFor="app">{t("appLabel")}</Label>
            <Select value={appSlug} onValueChange={setAppSlug}>
              <SelectTrigger id="app">
                <SelectValue placeholder={t("appPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {appList.map((a) => (
                  <SelectItem key={a.id} value={a.slug}>
                    {a.name}{" "}
                    <span className="text-muted-foreground">({a.slug})</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="env">{t("envLabel")}</Label>
            <Select
              value={environmentName}
              onValueChange={setEnvironmentName}
              disabled={!appSlug}
            >
              <SelectTrigger id="env">
                <SelectValue placeholder={t("envPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {envList.map((e) => (
                  <SelectItem key={e.id} value={e.name}>
                    {e.name}
                    {e.requiredApprovals > 0 && (
                      <span className="text-muted-foreground">
                        {" "}
                        · {t("approvalsSuffix", { count: e.requiredApprovals })}
                      </span>
                    )}
                    {e.deploysPaused && (
                      <span className="text-warning-fg"> · {t("paused")}</span>
                    )}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="tag">{t("tagLabel")}</Label>
            <Input
              id="tag"
              value={imageTag}
              onChange={(e) => setImageTag(e.target.value)}
              placeholder={t("tagPlaceholder")}
              className="font-mono text-xs"
              required
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="digest">{t("digestLabel")}</Label>
            <Input
              id="digest"
              value={imageDigest}
              onChange={(e) => setImageDigest(e.target.value)}
              placeholder={t("digestPlaceholder")}
              className="font-mono text-xs"
            />
            <p className="text-muted-foreground text-xs">{t("digestHint")}</p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="trigger">{t("triggerLabel")}</Label>
            <Select
              value={triggerKind}
              onValueChange={(v) =>
                setTriggerKind(v as (typeof TRIGGER_KINDS)[number])
              }
            >
              <SelectTrigger id="trigger">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TRIGGER_KINDS.map((k) => (
                  <SelectItem key={k} value={k}>
                    {k}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              {t("cancel")}
            </Button>
            <Button
              type="submit"
              disabled={loading || !appSlug || !environmentName || !imageTag}
            >
              {loading ? t("submitting") : t("submit")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
