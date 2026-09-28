"use client";

import { DownloadsScreen } from "@/components/screens/downloads/DownloadsScreen";
import { useDetectedPlatform } from "@/components/screens/downloads/use-detected-platform";

export function DownloadsClient() {
  return <DownloadsScreen {...useDetectedPlatform()} />;
}
