import { routeTitle } from "@/lib/i18n/route-title";

import { HelpClient } from "./help-client";

export const generateMetadata = routeTitle("getHelp", "documentation");

export default function HelpPage() {
  // Read platform version on the server so it ships in the initial
  // HTML. Falls back to the package.json version when the env var
  // isn't set.
  const platformVersion =
    process.env.NEXT_PUBLIC_PLATFORM_VERSION ?? process.env.npm_package_version ?? "0.1.0";

  return <HelpClient platformVersion={platformVersion} />;
}
