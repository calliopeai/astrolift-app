"use client";

import { useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  CheckIcon,
  CopyIcon,
  DownloadIcon,
  ExternalLinkIcon,
  TerminalIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { AppTabs } from "../components/app-tabs";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}

export function ConsoleClient({ slug }: { slug: string }) {
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.console");
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const a = app.data?.astroliftApp;

  const sampleCommands = [
    {
      key: "openShell" as const,
      label: t("shortcuts.openShell"),
      template: (s: string) => `astro exec --app=${s} -- bash`,
    },
    {
      key: "djangoCmd" as const,
      label: t("shortcuts.djangoCmd"),
      template: (s: string) =>
        `astro exec --app=${s} -- python manage.py migrate --check`,
    },
    {
      key: "tailLogs" as const,
      label: t("shortcuts.tailLogs"),
      template: (s: string) => `astro logs --app=${s} --follow`,
    },
  ];

  if (app.loading && !a) {
    return (
      <PageShell title={tCommon("loading")}>
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!a) {
    return (
      <PageShell title={tCommon("notFound")}>
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={tCommon("notFoundSlug", { slug })}
          description={tCommon("notFoundDescription")}
          actionHref="/apps"
          actionLabel={tCommon("backToApps")}
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={t("title", { name: a.name })}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {t("description", { slug: a.slug })}
        </span>
      }
    >
      <AppTabs slug={a.slug} active="console" />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <TerminalIcon className="size-4" />
            {t("terminal.title")}
            <Badge variant="outline" className="text-[10px] uppercase">
              {t("terminal.comingSoon")}
            </Badge>
          </CardTitle>
          <CardDescription>{t("terminal.description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="bg-muted/40 flex h-64 items-center justify-center rounded-md border border-dashed">
            <div className="text-muted-foreground flex flex-col items-center gap-2 text-center">
              <TerminalIcon className="size-8" />
              <p className="text-sm">{t("terminal.lands")}</p>
              <p className="max-w-md text-xs">
                {t.rich("terminal.untilThen", {
                  cli: () => (
                    <code className="bg-background rounded px-1 py-0.5 font-mono">astro</code>
                  ),
                })}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button asChild>
              <Link href="/downloads">
                <DownloadIcon className="size-4" />
                {t("terminal.install")}
              </Link>
            </Button>
            <Button asChild variant="outline">
              <Link href={`/apps/${a.slug}/tokens`}>
                <ExternalLinkIcon className="size-4" />
                {t("terminal.manageTokens")}
              </Link>
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("shortcuts.title")}</CardTitle>
          <CardDescription>
            {t.rich("shortcuts.description", {
              login: () => (
                <code className="bg-muted rounded px-1 py-0.5 font-mono">astro login</code>
              ),
            })}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {sampleCommands.map((s) => (
            <CopyableCommand
              key={s.key}
              label={s.label}
              command={s.template(a.slug)}
            />
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("lands.title")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <Item
            done
            label={t("lands.execLabel")}
            note={t("lands.execNote")}
            shippedLabel={t("lands.shipped")}
          />
          <Item
            label={t("lands.bridgeLabel")}
            note={t("lands.bridgeNote")}
            shippedLabel={t("lands.shipped")}
          />
          <Item
            label={t("lands.webLabel")}
            note={t("lands.webNote")}
            shippedLabel={t("lands.shipped")}
          />
        </CardContent>
      </Card>
    </PageShell>
  );
}

function CopyableCommand({
  label,
  command,
}: {
  label: string;
  command: string;
}) {
  return (
    <div>
      <div className="text-muted-foreground mb-1 text-xs uppercase tracking-wide">
        {label}
      </div>
      <div className="bg-muted flex items-start gap-2 rounded-md p-2">
        <pre className="flex-1 overflow-x-auto font-mono text-xs leading-relaxed whitespace-pre-wrap break-all">
          {command}
        </pre>
        <CopyButton value={command} />
      </div>
    </div>
  );
}

function CopyButton({ value }: { value: string }) {
  const t = useTranslations("apps.console");
  const [copied, setCopied] = React.useState(false);
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className="size-7 shrink-0"
      aria-label={t("copy")}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          toast.success(t("copied"));
          setTimeout(() => setCopied(false), 1200);
        } catch {
          toast.error(t("copyFailed"));
        }
      }}
    >
      {copied ? (
        <CheckIcon className="size-3.5" />
      ) : (
        <CopyIcon className="size-3.5" />
      )}
    </Button>
  );
}

function Item({
  done,
  label,
  note,
  shippedLabel,
}: {
  done?: boolean;
  label: string;
  note: string;
  shippedLabel: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <div
        className={`mt-1 size-2 shrink-0 rounded-full ${done ? "bg-[var(--brand-primary)]" : "bg-muted-foreground/40"}`}
      />
      <div>
        <div className="text-sm font-medium">
          {label}
          {done && (
            <Badge
              variant="secondary"
              className="ml-2 bg-emerald-500/15 text-emerald-700 text-xs dark:text-emerald-300"
            >
              {shippedLabel}
            </Badge>
          )}
        </div>
        <div className="text-muted-foreground text-xs">{note}</div>
      </div>
    </div>
  );
}
