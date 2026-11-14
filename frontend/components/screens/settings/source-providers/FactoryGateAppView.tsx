"use client";

import { GithubIcon, KeyRoundIcon, ShieldCheckIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DefinitionList } from "@/components/ui/definition-list";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@calliopelabs/ui";
import type { AstroliftFactoryGateApp } from "@/graphql/scm/scm.types";
import { cn } from "@/lib/utils";

import type { FactoryGateAppData } from "./use-factory-gate-app";

// A GitHub organization login, as the backend checks it before the manifest goes out.
const ORG_LOGIN = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/;

const STATE_TONE: Record<AstroliftFactoryGateApp["state"], string> = {
  NOT_REGISTERED: "",
  REGISTERED: "bg-warning/15 text-warning-fg",
  INSTALLED: "bg-success/15 text-success-fg",
  MISSING_KEY: "bg-danger/15 text-danger-fg",
};

export type FactoryGateAppViewProps = FactoryGateAppData & {
  /** The rotate key sheet; the route fills it with a container. */
  renderRotateKey: (
    open: boolean,
    onOpenChange: (next: boolean) => void,
    owner: string | null
  ) => React.ReactNode;
};

/**
 * Providers > Source > Factory gate app (#2589): the GitHub App the
 * Calliope AI factory reads organization membership with. Its state, ids and
 * one permission, the manifest-flow hand-off that registers or installs it,
 * and the key rotation. The key itself is never shown. Pure; the data half is
 * useFactoryGateApp.
 */
export function FactoryGateAppView({
  app,
  loading,
  error,
  onRetry,
  onRegister,
  renderRotateKey,
}: FactoryGateAppViewProps) {
  const t = useTranslations("sourceProviders.factoryGate");
  const [rotateOpen, setRotateOpen] = React.useState(false);
  const [org, setOrg] = React.useState("");
  const orgLogin = org.trim();
  const orgValid = ORG_LOGIN.test(orgLogin);
  const state = app?.state ?? "NOT_REGISTERED";
  const owner = app?.ownerLogin ?? null;

  function register(e: React.FormEvent) {
    e.preventDefault();
    if (orgValid) onRegister(orgLogin);
  }

  return (
    <Section
      headingLevel="h3"
      title={
        <span className="flex items-center gap-2">
          <ShieldCheckIcon className="size-4" />
          {t("title")}
        </span>
      }
      description={<span className="block max-w-2xl">{t("description")}</span>}
    >
      <Card>
        <CardContent className="flex min-w-0 flex-col gap-4">
          {loading ? (
            <div className="flex flex-col gap-2" aria-busy="true">
              <Skeleton className="h-5 w-32" />
              <Skeleton className="h-4 w-full max-w-md" />
              <Skeleton className="h-4 w-48" />
            </div>
          ) : error ? (
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span className="text-danger-fg">{t("loadFailed")}</span>
              <Button size="sm" variant="outline" onClick={onRetry}>
                {t("retry")}
              </Button>
            </div>
          ) : (
            <>
              <div className="flex min-w-0 flex-col gap-1.5">
                <Badge
                  variant={state === "NOT_REGISTERED" ? "outline" : "secondary"}
                  className={cn("w-fit", STATE_TONE[state])}
                >
                  {t(`state.${state}`)}
                </Badge>
                <p className="text-muted-foreground text-sm break-words">
                  {state === "REGISTERED" || state === "INSTALLED"
                    ? t(`stateHelp.${state}`, { owner: owner ?? "" })
                    : t(`stateHelp.${state}`)}
                </p>
              </div>

              {app?.appId && (
                <DefinitionList
                  className="text-xs"
                  items={[
                    {
                      term: t("appId"),
                      description: <code className="font-mono break-all">{app.appId}</code>,
                    },
                    {
                      term: t("installationId"),
                      description: app.installationId ? (
                        <code className="font-mono break-all">{app.installationId}</code>
                      ) : (
                        <span className="text-muted-foreground">{t("notSet")}</span>
                      ),
                    },
                    {
                      term: t("owner"),
                      description: owner ? (
                        <code className="font-mono break-all">{owner}</code>
                      ) : (
                        <span className="text-muted-foreground">{t("notSet")}</span>
                      ),
                    },
                    {
                      term: t("permissions"),
                      description: (
                        <span className="flex flex-wrap gap-1">
                          {app.permissions.map((p) => (
                            <Badge key={p.name} variant="outline" className="font-mono">
                              {`${p.name}: ${p.access}`}
                            </Badge>
                          ))}
                        </span>
                      ),
                    },
                  ]}
                />
              )}

              <Can permission="scm.connect">
                {state === "NOT_REGISTERED" ? (
                  <form onSubmit={register} className="flex max-w-md flex-col gap-2">
                    <Label htmlFor="factory-gate-org">{t("orgLabel")}</Label>
                    <div className="flex flex-wrap gap-2">
                      <Input
                        id="factory-gate-org"
                        value={org}
                        onChange={(e) => setOrg(e.target.value)}
                        placeholder="acme-corp"
                        className="min-w-0 flex-1 font-mono text-xs"
                        autoComplete="off"
                        spellCheck={false}
                      />
                      <Button type="submit" size="sm" disabled={!orgValid}>
                        <GithubIcon className="size-4" />
                        {t("register")}
                      </Button>
                    </div>
                    <p className="text-muted-foreground text-xs">{t("orgHelp")}</p>
                    {orgLogin !== "" && !orgValid && (
                      <p className="text-destructive text-xs">{t("orgInvalid")}</p>
                    )}
                  </form>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {state === "REGISTERED" && owner && (
                      <Button size="sm" onClick={() => onRegister(owner)}>
                        <GithubIcon className="size-4" />
                        {t("install")}
                      </Button>
                    )}
                    <Button size="sm" variant="outline" onClick={() => setRotateOpen(true)}>
                      <KeyRoundIcon className="size-4" />
                      {t("rotate")}
                    </Button>
                  </div>
                )}
              </Can>
            </>
          )}
        </CardContent>
      </Card>
      {renderRotateKey(rotateOpen, setRotateOpen, owner)}
    </Section>
  );
}
