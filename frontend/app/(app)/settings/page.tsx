import { SettingsLandingClient } from "./settings-landing-client";

export const metadata = { title: "Settings · Astrolift" };

/**
 * /settings landing — server component shell so the SSR-primed
 * Apollo cache from the (app) layout is available to the client
 * grid below without an extra request.
 */
export default function SettingsPage() {
  return <SettingsLandingClient />;
}
