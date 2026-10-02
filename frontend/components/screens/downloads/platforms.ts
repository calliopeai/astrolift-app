import { AppleIcon, MonitorIcon, TerminalIcon } from "lucide-react";
import type * as React from "react";

// Public release assets and their checksum manifest travel together.
export const CLI_REPO = "calliopeai/astrolift-cli";
export const CLI_BINARY = "astro";
export const RELEASES_URL = `https://github.com/${CLI_REPO}/releases`;
export const LATEST_URL = `${RELEASES_URL}/latest`;
export const CHECKSUMS_FILENAME = `${CLI_BINARY}-checksums.txt`;
const download = (asset: string, windows = false) => {
  const curl = windows ? "curl.exe" : "curl";
  return [asset, CHECKSUMS_FILENAME]
    .map((file) => `${curl} -fL '${LATEST_URL}/download/${file}' -o '${file}'`)
    .join(windows ? "; " : " && ");
};

export interface PlatformAsset {
  id: string;
  os: "macos" | "linux" | "windows";
  arch: "x64" | "arm64";
  label: string;
  filename: string;
  icon: React.ComponentType<{ className?: string }>;
  installLine: string;
}

export const PLATFORMS: PlatformAsset[] = [
  {
    id: "macos-arm64",
    os: "macos",
    arch: "arm64",
    label: "macOS · Apple Silicon",
    filename: `${CLI_BINARY}-darwin-arm64.tar.gz`,
    icon: AppleIcon,
    installLine: download(`${CLI_BINARY}-darwin-arm64.tar.gz`),
  },
  {
    id: "macos-x64",
    os: "macos",
    arch: "x64",
    label: "macOS · Intel",
    filename: `${CLI_BINARY}-darwin-amd64.tar.gz`,
    icon: AppleIcon,
    installLine: download(`${CLI_BINARY}-darwin-amd64.tar.gz`),
  },
  {
    id: "linux-x64",
    os: "linux",
    arch: "x64",
    label: "Linux · amd64",
    filename: `${CLI_BINARY}-linux-amd64.tar.gz`,
    icon: TerminalIcon,
    installLine: download(`${CLI_BINARY}-linux-amd64.tar.gz`),
  },
  {
    id: "linux-arm64",
    os: "linux",
    arch: "arm64",
    label: "Linux · arm64",
    filename: `${CLI_BINARY}-linux-arm64.tar.gz`,
    icon: TerminalIcon,
    installLine: download(`${CLI_BINARY}-linux-arm64.tar.gz`),
  },
  {
    id: "windows-x64",
    os: "windows",
    arch: "x64",
    label: "Windows · amd64",
    filename: `${CLI_BINARY}-windows-amd64.zip`,
    icon: MonitorIcon,
    installLine: download(`${CLI_BINARY}-windows-amd64.zip`, true),
  },
  {
    id: "windows-arm64",
    os: "windows",
    arch: "arm64",
    label: "Windows · arm64",
    filename: `${CLI_BINARY}-windows-arm64.zip`,
    icon: MonitorIcon,
    installLine: download(`${CLI_BINARY}-windows-arm64.zip`, true),
  },
];

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
export async function detectPlatform(): Promise<PlatformAsset | null> {
  if (typeof navigator === "undefined") return null;
  const ua = navigator.userAgent || "";
  const uaData = (navigator as Navigator & { userAgentData?: UADataLike }).userAgentData;

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
      const hev = await uaData.getHighEntropyValues(["architecture", "bitness"]);
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

  return PLATFORMS.find((p) => p.os === osHint && p.arch === archHint) ?? null;
}
