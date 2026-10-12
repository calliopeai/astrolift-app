"use client";

import {
  CheckIcon,
  CopyIcon,
  DownloadIcon,
  ExternalLinkIcon,
  PackageIcon,
  ShieldCheckIcon,
  SparklesIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import {
  CHECKSUMS_FILENAME,
  CLI_BINARY,
  CLI_REPO,
  LATEST_URL,
  PLATFORMS,
  type PlatformAsset,
  RELEASES_URL,
} from "./platforms";
import type { useDetectedPlatform } from "./use-detected-platform";

export type DownloadsScreenProps = ReturnType<typeof useDetectedPlatform>;

/** The CLI downloads page; `detected` is the visitor's platform, if known. */
export function DownloadsScreen({ detected, detectionDone }: DownloadsScreenProps) {
  const t = useTranslations("downloadsScreen");
  const otherPlatforms = React.useMemo(
    () => (detected ? PLATFORMS.filter((p) => p.id !== detected.id) : PLATFORMS),
    [detected]
  );

  return (
    <PageShell
      title={t("title")}
      description={t("description", { cli: CLI_BINARY })}
      actions={
        <Button asChild variant="outline">
          <a href={RELEASES_URL} target="_blank" rel="noreferrer">
            <ExternalLinkIcon className="size-4" />
            {t("allReleases")}
          </a>
        </Button>
      }
    >
      {/* ─── featured (detected) platform ──────────────────────────────── */}
      <FeaturedPlatformCallout platform={detected} detectionDone={detectionDone} />

      {/* ─── platform grid ─────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <DownloadIcon className="size-4" />{" "}
            {detected ? t("otherPlatforms") : t("nativeBinaries")}
          </CardTitle>
          <CardDescription>
            {t("binariesDescription", { file: CHECKSUMS_FILENAME })}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {otherPlatforms.map((p) => (
              <PlatformCard key={p.id} platform={p} />
            ))}
          </div>
        </CardContent>
      </Card>

      {/* ─── install channels ──────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <PackageIcon className="size-4" /> {t("channels")}
          </CardTitle>
          <CardDescription>{t("channelsDescription")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <PackageRow
            name="GitHub CLI"
            command={`gh release view --repo ${CLI_REPO} --web`}
            note={t("ghNote")}
          />
          <PackageRow
            name="Docker"
            command="docker run --rm docker.io/calliopeai/astrolift-cli:latest version"
            note={t("dockerNote")}
          />
        </CardContent>
      </Card>

      {/* ─── verification ──────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldCheckIcon className="size-4" /> {t("verify")}
          </CardTitle>
          <CardDescription>{t("verifyDescription", { file: CHECKSUMS_FILENAME })}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <CopyableCommand
            label={t("fetchChecksums")}
            command={`curl -fL '${LATEST_URL}/download/${CHECKSUMS_FILENAME}' -o '${CHECKSUMS_FILENAME}'`}
          />
          <CopyableCommand
            label={t("verifyUnix")}
            command={`shasum -a 256 ${CLI_BINARY}-*.tar.gz`}
          />
          <CopyableCommand
            label={t("verifyWindows")}
            command={`Get-FileHash ${CLI_BINARY}-windows-*.zip -Algorithm SHA256`}
          />
          <p className="text-muted-foreground text-xs">
            {t.rich("compareDigest", {
              link: (chunks) => (
                <a href={LATEST_URL} target="_blank" rel="noreferrer" className="underline">
                  {chunks}
                </a>
              ),
            })}
          </p>
        </CardContent>
      </Card>

      {/* ─── quickstart ────────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("afterInstalling")}</CardTitle>
          <CardDescription>{t("afterInstallingDescription")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <CopyableCommand label={t("authenticate")} command={`${CLI_BINARY} auth login`} />
          <CopyableCommand label={t("listApps")} command={`${CLI_BINARY} app list`} />
          <Link
            href="/settings"
            className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs"
          >
            {t("manageTokens")} <ExternalLinkIcon className="size-3" />
          </Link>
        </CardContent>
      </Card>
    </PageShell>
  );
}

function FeaturedPlatformCallout({
  platform,
  detectionDone,
}: {
  platform: PlatformAsset | null;
  detectionDone: boolean;
}) {
  // Pre-detection (SSR + first paint): render a neutral placeholder so
  // the layout doesn't jump. After detection settles, show either the
  // matched platform or a "couldn't detect" fallback.
  const t = useTranslations("downloadsScreen");
  if (!detectionDone) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <SparklesIcon className="size-4" /> {t("forYourSystem")}
          </CardTitle>
          <CardDescription>{t("detecting")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="border-border bg-muted/30 h-28 animate-pulse rounded-md border" />
        </CardContent>
      </Card>
    );
  }

  if (!platform) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <SparklesIcon className="size-4" /> {t("forYourSystem")}
          </CardTitle>
          <CardDescription>{t("detectFailed")}</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const Icon = platform.icon;
  return (
    <Card className="border-primary/40 bg-primary/[0.03]">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <SparklesIcon className="size-4" /> {t("forYourSystem")}
        </CardTitle>
        <CardDescription>{t("detected", { platform: platform.label })}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="bg-primary/10 text-primary rounded-md p-2">
            <Icon className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold">{platform.label}</span>
              <Badge className="text-2xs tracking-wide uppercase">{t("recommended")}</Badge>
            </div>
            <div className="text-muted-foreground font-mono text-xs">{platform.filename}</div>
          </div>
          <Button asChild size="sm">
            <a href={LATEST_URL} target="_blank" rel="noreferrer">
              <DownloadIcon className="size-3" />
              {t("openRelease")}
            </a>
          </Button>
        </div>
        <CopyableCommand label={t("download")} command={platform.installLine} />
      </CardContent>
    </Card>
  );
}

function PlatformCard({ platform }: { platform: PlatformAsset }) {
  const t = useTranslations("downloadsScreen");
  const Icon = platform.icon;
  return (
    <div className="border-border bg-card flex flex-col gap-3 rounded-md border p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <div className="bg-primary/10 text-primary rounded-md p-1.5">
            <Icon className="size-4" />
          </div>
          <div>
            <div className="text-sm font-medium">{platform.label}</div>
            <div className="text-muted-foreground font-mono text-xs">{platform.arch}</div>
          </div>
        </div>
      </div>
      <div className="text-muted-foreground truncate font-mono text-xs">{platform.filename}</div>
      <div className="flex items-center gap-2">
        <Button asChild size="sm" className="flex-1">
          <a href={LATEST_URL} target="_blank" rel="noreferrer">
            <DownloadIcon className="size-3" />
            {t("openRelease")}
          </a>
        </Button>
        <CopyButton value={platform.installLine} label={t("copyDownload")} />
      </div>
    </div>
  );
}

function CopyableCommand({ label, command }: { label: string; command: string }) {
  const t = useTranslations("downloadsScreen");
  return (
    <div>
      <div className="text-muted-foreground mb-1 text-xs tracking-wide uppercase">{label}</div>
      <div className="bg-muted flex items-start gap-2 rounded-md p-2">
        <pre className="flex-1 overflow-x-auto font-mono text-xs leading-relaxed break-all whitespace-pre-wrap">
          {command}
        </pre>
        <CopyButton value={command} label={t("copyLabel", { label })} />
      </div>
    </div>
  );
}

function PackageRow({ name, command, note }: { name: string; command: string; note?: string }) {
  const t = useTranslations("downloadsScreen");
  return (
    <div className="border-border rounded-md border p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{name}</span>
      </div>
      <div className="bg-muted flex items-start gap-2 rounded-md p-2">
        <pre className="flex-1 overflow-x-auto font-mono text-xs leading-relaxed break-all whitespace-pre-wrap">
          {command}
        </pre>
        <CopyButton value={command} label={t("copyInstall", { name })} />
      </div>
      {note && <p className="text-muted-foreground mt-2 text-xs">{note}</p>}
    </div>
  );
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const t = useTranslations("downloadsScreen");
  const [copied, setCopied] = React.useState(false);
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className="size-7 shrink-0"
      aria-label={label}
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
      {copied ? <CheckIcon className="size-3.5" /> : <CopyIcon className="size-3.5" />}
    </Button>
  );
}
