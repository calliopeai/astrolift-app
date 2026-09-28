import { LogsScreen } from "@/components/screens/logs/LogsScreen";

export const metadata = { title: "Logs · Astrolift" };

/**
 * Logs — fleet-wide log aggregation and search.
 *
 * Per-app / per-pod log streaming is available today from the app Console
 * tab. This page will surface a cross-app log explorer: search, filter by
 * severity, time range, app, and workload — powered by the platform's
 * observability backend.
 *
 * Gateway page while the fleet-level log aggregation surface is under
 * development. Per-app logs are available now from each app's Console tab.
 */
export default function LogsPage() {
  return <LogsScreen />;
}
