/**
 * Logs & metrics › Metrics, one panel at a time (Leo's page rules 1 to 3):
 * Signals (the scope picker and the metric panels), Pods (the pod list, the
 * picked pod's usage, log and events), Alerts (the alert rules and the
 * picked rule's events) and Network (DNS, TLS, workload identity). The panel
 * is `?panel=` under `?section=metrics`; only the one on screen mounts and
 * fetches.
 *
 * The pods and the alert rules are lists. `astroliftAppPods` returns every
 * pod at once, and the rules read `astroliftAlertRules`, which caps at 200
 * rows with no filter or sort, so both are answered in the browser with
 * numbered pages and a note saying so. Pure.
 */
import type { ListDefinition } from "@/components/list/use-list-state";
import type { AstroliftAppPod } from "@/graphql/lifecycle/lifecycle.types";

import { CLIENT_LIST_NOTE, type ClientListSpec, selectClientRows } from "../client-list";
import type { AlertRule } from "./use-alert-rules";

export const METRICS_PANELS = ["signals", "pods", "alerts", "network"] as const;
export type MetricsPanel = (typeof METRICS_PANELS)[number];

export const METRICS_PANEL_LABELS: Record<MetricsPanel, string> = {
  signals: "Signals",
  pods: "Pods",
  alerts: "Alert rules",
  network: "DNS & TLS",
};

/** `?panel=` to a panel; anything else is Signals. */
export function metricsPanel(raw: string | string[] | null | undefined): MetricsPanel {
  return (METRICS_PANELS as readonly string[]).includes(raw as string)
    ? (raw as MetricsPanel)
    : "signals";
}

/** Links to a panel keep `?section=metrics` and the metric scope, and drop the rest. */
export function metricsPanelQuery(current: string, panel: MetricsPanel): string {
  const from = new URLSearchParams(current);
  const next = new URLSearchParams({ section: "metrics" });
  if (panel !== "signals") next.set("panel", panel);
  for (const key of ["env", "workload"]) {
    const value = from.get(key);
    if (value) next.set(key, value);
  }
  return next.toString();
}

export const APP_PODS_LIST: ListDefinition = {
  id: "apps.metrics.pods",
  fields: [
    {
      key: "status",
      label: "Status",
      options: [
        { value: "Running", label: "Running" },
        { value: "Pending", label: "Pending" },
        { value: "Failed", label: "Failed" },
        { value: "CrashLoopBackOff", label: "CrashLoopBackOff" },
        { value: "Succeeded", label: "Succeeded" },
      ],
    },
  ],
  searchPlaceholder: "Search pods, workloads, nodes…",
  defaultSort: [{ key: "name", dir: "asc" }],
  // A pod has no owner, so there is no Mine: one view.
  views: [{ key: "all", label: "All", filters: {}, note: CLIENT_LIST_NOTE }],
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

const PODS_SPEC: ClientListSpec<AstroliftAppPod> = {
  matches: (p, key, value) => (key === "status" ? p.status === value : true),
  text: (p) => [p.name, p.workload, p.node, p.status],
  compare: (a, b, key) => {
    switch (key) {
      case "workload":
        return a.workload.localeCompare(b.workload);
      case "status":
        return a.status.localeCompare(b.status);
      case "restarts":
        return a.restarts - b.restarts;
      case "age":
        // Oldest first ascending: an earlier start is a greater age.
        return (Date.parse(b.age ?? "") || 0) - (Date.parse(a.age ?? "") || 0);
      case "name":
      default:
        return a.name.localeCompare(b.name);
    }
  },
  tie: (a, b) => a.name.localeCompare(b.name),
};

export function selectPods(
  all: AstroliftAppPod[],
  filters: Record<string, string>,
  state: Parameters<typeof selectClientRows>[2]
) {
  return selectClientRows(all, filters, state, PODS_SPEC);
}

export const APP_ALERT_RULES_LIST: ListDefinition = {
  id: "apps.metrics.alert-rules",
  fields: [
    {
      key: "severity",
      label: "Severity",
      options: [
        { value: "critical", label: "Critical" },
        { value: "warning", label: "Warning" },
        { value: "info", label: "Info" },
      ],
    },
    {
      key: "state",
      label: "State",
      options: [
        { value: "active", label: "Active" },
        { value: "muted", label: "Muted" },
        { value: "inactive", label: "Inactive" },
      ],
    },
  ],
  searchPlaceholder: "Search alert rules…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: [{ key: "all", label: "All", filters: {}, note: CLIENT_LIST_NOTE }],
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

function ruleState(r: AlertRule): "active" | "muted" | "inactive" {
  if (r.activeMute) return "muted";
  return r.isActive ? "active" : "inactive";
}

const RULES_SPEC: ClientListSpec<AlertRule> = {
  matches: (r, key, value) => {
    if (key === "severity") return r.severity === value;
    if (key === "state") return ruleState(r) === value;
    return true;
  },
  text: (r) => [r.name, r.severity],
  compare: (a, b, key) => {
    switch (key) {
      case "severity":
        return a.severity.localeCompare(b.severity);
      case "created":
        return (Date.parse(a.createdAt) || 0) - (Date.parse(b.createdAt) || 0);
      case "name":
      default:
        return a.name.localeCompare(b.name);
    }
  },
  tie: (a, b) => a.id.localeCompare(b.id),
};

export function selectAlertRules(
  all: AlertRule[],
  filters: Record<string, string>,
  state: Parameters<typeof selectClientRows>[2]
) {
  return selectClientRows(all, filters, state, RULES_SPEC);
}
