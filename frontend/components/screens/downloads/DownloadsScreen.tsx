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
  const otherPlatforms = React.useMemo(
    () => (detected ? PLATFORMS.filter((p) => p.id !== detected.id) : PLATFORMS),
    [detected]
  );

  return (
    <PageShell
      title="Downloads"
      description={`Install the ${CLI_BINARY} CLI to interact with Astrolift from your shell or CI. Native releases require an authorized GitHub identity; the container image is public.`}
      actions={
        <Button asChild variant="outline">
          <a href={RELEASES_URL} target="_blank" rel="noreferrer">
            <ExternalLinkIcon className="size-4" />
            All releases
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
            <DownloadIcon className="size-4" /> {detected ? "Other platforms" : "Native binaries"}
          </CardTitle>
          <CardDescription>
            Single-file static binaries. Run `gh auth login` with an account that can read the CLI
            repository before using these commands.
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
            <PackageIcon className="size-4" /> Install channels
          </CardTitle>
          <CardDescription>
            Native archives are private. Homebrew, Scoop, anonymous curl, and direct asset links
            cannot authenticate them and are not supported yet.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <PackageRow
            name="GitHub CLI"
            command={`gh auth login; gh release view --repo ${CLI_REPO} --web`}
            note="Authenticate first, then use the matching command above. Each command also fetches the release checksum list."
          />
          <PackageRow
            name="Docker"
            command="docker run --rm docker.io/calliopeai/astrolift-cli:latest version"
            note="Public multi-architecture image for CI agents that do not need a host install."
          />
        </CardContent>
      </Card>

      {/* ─── verification ──────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldCheckIcon className="size-4" /> Verify your download
          </CardTitle>
          <CardDescription>
            Every release publishes a {CHECKSUMS_FILENAME} file with SHA-256 sums for each archive.
            Verify before extracting.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <CopyableCommand
            label="Fetch checksums (authenticated)"
            command={`gh release download --repo ${CLI_REPO} --pattern '${CHECKSUMS_FILENAME}'`}
          />
          <CopyableCommand
            label="Verify (macOS/Linux)"
            command={`shasum -a 256 -c ${CHECKSUMS_FILENAME} --ignore-missing`}
          />
          <CopyableCommand
            label="Verify (Windows PowerShell)"
            command={`Get-FileHash ${CLI_BINARY}-windows-amd64.zip -Algorithm SHA256`}
          />
          <p className="text-muted-foreground text-xs">
            Compare the computed digest with the checksum published alongside the archive on{" "}
            <a href={LATEST_URL} target="_blank" rel="noreferrer" className="underline">
              GitHub
            </a>{" "}
            before installing.
          </p>
        </CardContent>
      </Card>

      {/* ─── quickstart ────────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">After installing</CardTitle>
          <CardDescription>
            Log in to this Astrolift install and list your apps to confirm the CLI is wired up.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <CopyableCommand label="Authenticate" command={`${CLI_BINARY} auth login`} />
          <CopyableCommand label="List apps" command={`${CLI_BINARY} app list`} />
          <Link
            href="/settings"
            className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs"
          >
            Manage API tokens <ExternalLinkIcon className="size-3" />
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
  if (!detectionDone) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <SparklesIcon className="size-4" /> For your system
          </CardTitle>
          <CardDescription>Detecting your platform…</CardDescription>
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
            <SparklesIcon className="size-4" /> For your system
          </CardTitle>
          <CardDescription>
            We couldn&apos;t detect your platform automatically. Pick the matching native binary
            below.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const Icon = platform.icon;
  return (
    <Card className="border-primary/40 bg-primary/[0.03]">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <SparklesIcon className="size-4" /> For your system
        </CardTitle>
        <CardDescription>
          We detected {platform.label}. This is the one-liner you want.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="bg-primary/10 text-primary rounded-md p-2">
            <Icon className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold">{platform.label}</span>
              <Badge className="text-2xs tracking-wide uppercase">
                Recommended for your system
              </Badge>
            </div>
            <div className="text-muted-foreground font-mono text-xs">{platform.filename}</div>
          </div>
          <Button asChild size="sm">
            <a href={LATEST_URL} target="_blank" rel="noreferrer">
              <DownloadIcon className="size-3" />
              Open release
            </a>
          </Button>
        </div>
        <CopyableCommand label="Install" command={platform.installLine} />
      </CardContent>
    </Card>
  );
}

function PlatformCard({ platform }: { platform: PlatformAsset }) {
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
            Open release
          </a>
        </Button>
        <CopyButton value={platform.installLine} label="Copy install line" />
      </div>
    </div>
  );
}

function CopyableCommand({ label, command }: { label: string; command: string }) {
  return (
    <div>
      <div className="text-muted-foreground mb-1 text-xs tracking-wide uppercase">{label}</div>
      <div className="bg-muted flex items-start gap-2 rounded-md p-2">
        <pre className="flex-1 overflow-x-auto font-mono text-xs leading-relaxed break-all whitespace-pre-wrap">
          {command}
        </pre>
        <CopyButton value={command} label={`Copy ${label}`} />
      </div>
    </div>
  );
}

function PackageRow({ name, command, note }: { name: string; command: string; note?: string }) {
  return (
    <div className="border-border rounded-md border p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{name}</span>
      </div>
      <div className="bg-muted flex items-start gap-2 rounded-md p-2">
        <pre className="flex-1 overflow-x-auto font-mono text-xs leading-relaxed break-all whitespace-pre-wrap">
          {command}
        </pre>
        <CopyButton value={command} label={`Copy ${name} install`} />
      </div>
      {note && <p className="text-muted-foreground mt-2 text-xs">{note}</p>}
    </div>
  );
}

function CopyButton({ value, label }: { value: string; label: string }) {
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
          toast.success("Copied to clipboard");
          setTimeout(() => setCopied(false), 1200);
        } catch {
          toast.error("Couldn't copy — clipboard access blocked");
        }
      }}
    >
      {copied ? <CheckIcon className="size-3.5" /> : <CopyIcon className="size-3.5" />}
    </Button>
  );
}
