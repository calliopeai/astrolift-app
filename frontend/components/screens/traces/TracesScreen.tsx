"use client";
import { TelemetryExplorerScreen } from "../telemetry/TelemetryExplorerScreen";
import { useScopedExplorer, type ExplorerProps } from "../telemetry/use-scoped-explorer";
export function TracesScreen(props: ExplorerProps) {
  return <TelemetryExplorerScreen {...props} mode="traces" />;
}
export function TracesExplorer() {
  return <TracesScreen {...useScopedExplorer("traces")} />;
}
