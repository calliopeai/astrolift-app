import { TracesScreen } from "@/components/screens/traces/TracesScreen";

export const metadata = { title: "Traces · Astrolift" };

/**
 * Traces — distributed trace explorer.
 *
 * Astrolift already emits OpenTelemetry traces from the control plane and
 * can collect traces from tenant workloads via the OTEL collector sidecar.
 * Per-app traces are visible today in each app's Observability tab.
 *
 * This page will be the fleet-wide trace explorer: search by service,
 * operation, status, duration, and trace ID. The backend GraphQL already
 * exposes `astroliftAppTraces` and `astroliftTraceSpans`.
 *
 * Gateway page while the fleet-level trace explorer is under development.
 * Per-app traces are available now from each app's Observability tab.
 */
export default function TracesPage() {
  return <TracesScreen />;
}
