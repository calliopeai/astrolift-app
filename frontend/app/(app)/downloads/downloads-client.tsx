"use client";

import {
  AppleIcon,
  CheckIcon,
  CopyIcon,
  DownloadIcon,
  ExternalLinkIcon,
  MonitorIcon,
  PackageIcon,
  ShieldCheckIcon,
  SparklesIcon,
  TerminalIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

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

// The CLI repo's GitHub Releases is the source of truth for binaries.
// The "latest" alias resolves to whatever the most recent published
// release is, so we don't have to keep this page in lockstep with the
// CLI's release cadence. When the platform grows a Releases GraphQL
// query (#TBD) we can swap these constants for live data including
// SHA-256 sums and per-asset sizes.
const CLI_REPO = "calliopeai/astrolift-cli";
const CLI_BINARY = "astro";
const RELEASES_URL = `https://github.com/${CLI_REPO}/releases`;
const LATEST_URL = `${RELEASES_URL}/latest`;
const LATEST_ASSET = (asset: string) =>
  `${RELEASES_URL}/latest/download/${asset}`;

interface PlatformAsset {
  id: string;
  os: "macos" | "linux" | "windows";
  arch: "x64" | "arm64";
  label: string;
  filename: string;
  icon: React.ComponentType<{ className?: string }>;
  installLine: string;
}

const PLATFORMS: PlatformAsset[] = [
  {
    id: "macos-arm64",
    os: "macos",
    arch: "arm64",
    label: "macOS · Apple Silicon",
    filename: `${CLI_BINARY}-darwin-arm64.tar.gz`,
    icon: AppleIcon,
    installLine: `curl -fsSL ${LATEST_ASSET(`${CLI_BINARY}-darwin-arm64.tar.gz`)} | tar -xz && sudo mv ${CLI_BINARY} /usr/local/bin/`,
  },
  {
    id: "macos-x64",
    os: "macos",
    arch: "x64",
    label: "macOS · Intel",
    filename: `${CLI_BINARY}-darwin-amd64.tar.gz`,
    icon: AppleIcon,
    installLine: `curl -fsSL ${LATEST_ASSET(`${CLI_BINARY}-darwin-amd64.tar.gz`)} | tar -xz && sudo mv ${CLI_BINARY} /usr/local/bin/`,
  },
  {
    id: "linux-x64",
    os: "linux",
    arch: "x64",
    label: "Linux · amd64",
    filename: `${CLI_BINARY}-linux-amd64.tar.gz`,
    icon: TerminalIcon,
    installLine: `curl -fsSL ${LATEST_ASSET(`${CLI_BINARY}-linux-amd64.tar.gz`)} | tar -xz && sudo mv ${CLI_BINARY} /usr/local/bin/`,
  },
  {
    id: "linux-arm64",
    os: "linux",
    arch: "arm64",
    label: "Linux · arm64",
    filename: `${CLI_BINARY}-linux-arm64.tar.gz`,
    icon: TerminalIcon,
    installLine: `curl -fsSL ${LATEST_ASSET(`${CLI_BINARY}-linux-arm64.tar.gz`)} | tar -xz && sudo mv ${CLI_BINARY} /usr/local/bin/`,
  },
  {
    id: "windows-x64",
    os: "windows",
    arch: "x64",
    label: "Windows · amd64",
    filename: `${CLI_BINARY}-windows-amd64.zip`,
    icon: MonitorIcon,
    installLine: `irm ${LATEST_ASSET(`${CLI_BINARY}-windows-amd64.zip`)} -OutFile ${CLI_BINARY}.zip; Expand-Archive ${CLI_BINARY}.zip`,
  },
];

const CHECKSUMS_FILENAME = `${CLI_BINARY}-checksums.txt`;

// Minimal typing for the User-Agent Client Hints API. As of 2026 the API
// ships in Chromium-family browsers (Chrome/Edge/Opera) but not Firefox
// or Safari. We feature-detect at runtime and fall back to UA-string
// sniffing — see detectPlatform().
interface UADataValues {
  architecture?: string;
  bitness?: string;
  platform?: string;
}
interface UADataLike {
  platform?: string;
  getHighEntropyValues?: (hints: string[]) => Promise<UADataValues>;
}

// Resolve the visitor's PlatformAsset from navigator hints. Returns
// `null` when detection is inconclusive (SSR, headless, unrecognized
// UA) so the UI can render a neutral fallback. Detection is best-
// effort: we never hard-fail, and we don't pretend to know more than
// we do. macOS UA strings cannot reliably distinguish Intel vs Apple
// Silicon from the legacy `navigator.platform`/userAgent surface, so
// we use Client Hints when available and default to arm64 otherwise
// (the dominant macOS architecture in 2026).
async function detectPlatform(): Promise<PlatformAsset | null> {
  if (typeof navigator === "undefined") return null;
  const ua = navigator.userAgent || "";
  const uaData = (navigator as Navigator & { userAgentData?: UADataLike })
    .userAgentData;

  let osHint: "macos" | "linux" | "windows" | null = null;
  if (uaData?.platform) {
    const p = uaData.platform.toLowerCase();
    if (p.includes("mac")) osHint = "macos";
    else if (p.includes("linux") || p.includes("android")) osHint = "linux";
    else if (p.includes("win")) osHint = "windows";
  }
  if (!osHint) {
    if (/Mac|iPhone|iPad|iPod/i.test(ua)) osHint = "macos";
    else if (/Windows/i.test(ua)) osHint = "windows";
    else if (/Linux|X11|CrOS|Android/i.test(ua)) osHint = "linux";
  }
  if (!osHint) return null;

  // Architecture: prefer high-entropy Client Hints, then UA string,
  // then OS-specific defaults.
  let archHint: "arm64" | "x64" | null = null;
  if (uaData?.getHighEntropyValues) {
    try {
      const hev = await uaData.getHighEntropyValues([
        "architecture",
        "bitness",
      ]);
      const arch = (hev.architecture || "").toLowerCase();
      if (arch === "arm" || arch === "arm64") archHint = "arm64";
      else if (arch === "x86") archHint = "x64";
    } catch {
      // Some browsers reject high-entropy queries silently; fall through.
    }
  }
  if (!archHint) {
    if (/aarch64|arm64/i.test(ua)) archHint = "arm64";
    else if (/x86_64|x64|Win64|WOW64|amd64/i.test(ua)) archHint = "x64";
  }
  if (!archHint) {
    // macOS in 2026 is overwhelmingly Apple Silicon; Linux/Windows
    // browser fleets still skew x64. These are starting points, not
    // guarantees — the user can pick a different card.
    archHint = osHint === "macos" ? "arm64" : "x64";
  }

  // Windows only ships an x64 archive today; collapse arm64 → x64.
  if (osHint === "windows") archHint = "x64";

  return (
    PLATFORMS.find((p) => p.os === osHint && p.arch === archHint) ?? null
  );
}

export function DownloadsClient() {
  const [detected, setDetected] = React.useState<PlatformAsset | null>(null);
  const [detectionDone, setDetectionDone] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    void detectPlatform().then((p) => {
      if (cancelled) return;
      setDetected(p);
      setDetectionDone(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const otherPlatforms = React.useMemo(
    () => (detected ? PLATFORMS.filter((p) => p.id !== detected.id) : PLATFORMS),
    [detected],
  );

  return (
    <PageShell
      title="Downloads"
      description={`Install the ${CLI_BINARY} CLI to interact with Astrolift from your shell or CI. Pick a platform below or use a package manager.`}
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
      <FeaturedPlatformCallout
        platform={detected}
        detectionDone={detectionDone}
      />

      {/* ─── platform grid ─────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <DownloadIcon className="size-4" />{" "}
            {detected ? "Other platforms" : "Native binaries"}
          </CardTitle>
          <CardDescription>
            Single-file static binaries. Download and run — no runtime
            required. The latest alias always resolves to the newest
            published release.
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

      {/* ─── package managers ──────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <PackageIcon className="size-4" /> Package managers
          </CardTitle>
          <CardDescription>
            Pinned-version installs for teams that prefer a package
            manager over downloading a binary directly.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <PackageRow
            name="Homebrew"
            badge="coming soon"
            command={`brew install calliopeai/tap/${CLI_BINARY}`}
            note="Tap publication tracking on the CLI side. Use the macOS binary above in the meantime."
            disabled
          />
          <PackageRow
            name="npm"
            command={`npm install -g @calliopelabs/${CLI_BINARY}`}
            note={`Cross-platform install via Node.js (≥ 20). The npm package thin-wraps the same native binaries as the direct downloads.`}
          />
          <PackageRow
            name="Docker"
            command={`docker run --rm -v $PWD:/work calliopeai/${CLI_BINARY}:latest`}
            note="Containerized run useful for CI agents that don't want to manage host installs."
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
            Every release publishes a {CHECKSUMS_FILENAME} file with
            SHA-256 sums for each archive. Verify before extracting.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <CopyableCommand
            label="Fetch checksums"
            command={`curl -fsSLO ${LATEST_ASSET(CHECKSUMS_FILENAME)}`}
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
            Checksums are signed with the Astrolift release key — see the
            release notes on{" "}
            <a
              href={LATEST_URL}
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              GitHub
            </a>{" "}
            for the detached signature.
          </p>
        </CardContent>
      </Card>

      {/* ─── quickstart ────────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">After installing</CardTitle>
          <CardDescription>
            Log in to this Astrolift install and list your apps to confirm
            the CLI is wired up.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <CopyableCommand
            label="Authenticate"
            command={`${CLI_BINARY} login`}
          />
          <CopyableCommand
            label="List apps"
            command={`${CLI_BINARY} apps list`}
          />
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
            We couldn&apos;t detect your platform automatically. Pick the
            matching native binary below.
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
              <Badge className="text-[10px] uppercase tracking-wide">
                Recommended for your system
              </Badge>
            </div>
            <div className="text-muted-foreground font-mono text-xs">
              {platform.filename}
            </div>
          </div>
          <Button asChild size="sm">
            <a
              href={LATEST_ASSET(platform.filename)}
              target="_blank"
              rel="noreferrer"
            >
              <DownloadIcon className="size-3" />
              Download
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
            <div className="text-muted-foreground font-mono text-xs">
              {platform.arch}
            </div>
          </div>
        </div>
      </div>
      <div className="text-muted-foreground truncate font-mono text-xs">
        {platform.filename}
      </div>
      <div className="flex items-center gap-2">
        <Button asChild size="sm" className="flex-1">
          <a
            href={LATEST_ASSET(platform.filename)}
            target="_blank"
            rel="noreferrer"
          >
            <DownloadIcon className="size-3" />
            Download
          </a>
        </Button>
        <CopyButton
          value={platform.installLine}
          label="Copy install line"
        />
      </div>
    </div>
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
        <CopyButton value={command} label={`Copy ${label}`} />
      </div>
    </div>
  );
}

function PackageRow({
  name,
  command,
  note,
  badge,
  disabled,
}: {
  name: string;
  command: string;
  note?: string;
  badge?: string;
  disabled?: boolean;
}) {
  return (
    <div className="border-border rounded-md border p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{name}</span>
        {badge && (
          <Badge variant="outline" className="text-[10px] uppercase">
            {badge}
          </Badge>
        )}
      </div>
      <div className="bg-muted flex items-start gap-2 rounded-md p-2">
        <pre
          className={`flex-1 overflow-x-auto font-mono text-xs leading-relaxed whitespace-pre-wrap break-all ${disabled ? "opacity-60" : ""}`}
        >
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
      {copied ? (
        <CheckIcon className="size-3.5" />
      ) : (
        <CopyIcon className="size-3.5" />
      )}
    </Button>
  );
}
