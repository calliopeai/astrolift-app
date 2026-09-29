"use client";

import { HelpScreen } from "@/components/screens/documentation/HelpScreen";
import { useHelp } from "@/components/screens/documentation/use-help";

interface HelpClientProps {
  platformVersion: string;
}

export function HelpClient({ platformVersion }: HelpClientProps) {
  const help = useHelp(platformVersion);
  return <HelpScreen platformVersion={platformVersion} {...help} />;
}
