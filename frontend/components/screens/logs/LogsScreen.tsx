"use client";
import { TelemetryExplorerScreen } from "../telemetry/TelemetryExplorerScreen";
import { useScopedExplorer, type ExplorerProps } from "../telemetry/use-scoped-explorer";
export function LogsScreen(props: ExplorerProps) {
  return <TelemetryExplorerScreen {...props} mode="logs" />;
}
export function LogsExplorer() {
  return <LogsScreen {...useScopedExplorer("logs")} />;
}
