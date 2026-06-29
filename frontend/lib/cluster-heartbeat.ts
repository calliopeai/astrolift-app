/**
 * Cluster keep-alive heartbeat presentation helpers (#808).
 *
 * The backend derives a live status from the cluster's last heartbeat
 * (see ``heartbeat_status`` policy). This module is the single source
 * of truth for how that status renders across the Clusters list, the
 * cluster Status tab, and the dependent app tabs — so a status badge
 * looks identical everywhere and the "is the cluster reachable?"
 * decision is made one way.
 *
 * Typed locally rather than off the generated schema: the repo's
 * committed GraphQL codegen output lags the live backend, so the
 * generated ``AstroliftTenantCluster`` doesn't yet carry these fields.
 * The client components type the heartbeat surface here instead — the
 * same inline-interface pattern the cluster Status tab already uses.
 */

export type HeartbeatStatus = "never_seen" | "connected" | "degraded" | "offline";

/** Heartbeat fields the backend adds to every cluster row (#808). */
export interface ClusterHeartbeatFields {
  lastHeartbeatAt: string | null;
  heartbeatIntervalSeconds: number;
  heartbeatStatus: HeartbeatStatus;
  heartbeatAgeSeconds: number | null;
  agentProvisioned: boolean;
}

/** Per-app pod readiness reported by the keep-alive agent (#112). */
export interface AppReadiness {
  ready: number;
  total: number;
}

/** Full live-state snapshot returned by ``astroliftClusterLiveState``. */
export interface ClusterLiveState {
  clusterId: string;
  status: HeartbeatStatus;
  lastHeartbeatAt: string | null;
  heartbeatAgeSeconds: number | null;
  heartbeatIntervalSeconds: number;
  agentProvisioned: boolean;
  nodeCount: number | null;
  nodeReadyCount: number | null;
  cpuUtilization: number | null;
  memoryUtilization: number | null;
  podTotal: number | null;
  podsByNamespace: Record<string, number>;
  appReadiness: Record<string, AppReadiness>;
  ingressIps: string[];
  agentVersion: string;
}

/**
 * Whether the cluster is reachable enough to attempt a live pull.
 * CONNECTED and DEGRADED both mean "the agent is (mostly) talking to
 * us" — the dependent cards should still try their driver / Prometheus
 * queries. NEVER_SEEN and OFFLINE are the empty-state cases that should
 * short-circuit instead of spinning. Mirrors the backend ``is_live``.
 */
export function isClusterLive(status: HeartbeatStatus): boolean {
  return status === "connected" || status === "degraded";
}

export interface HeartbeatPresentation {
  label: string;
  /** StatusDot tone. */
  dot: "ok" | "warn" | "error" | "muted";
  /** Badge variant. */
  variant: "default" | "secondary" | "outline" | "destructive";
  /** Tailwind text/border classes for inline pills. */
  pill: string;
}

const PRESENTATION: Record<HeartbeatStatus, HeartbeatPresentation> = {
  connected: {
    label: "Connected",
    dot: "ok",
    variant: "default",
    pill: "border-emerald-500/40 text-emerald-700 dark:text-emerald-400 bg-emerald-500/10",
  },
  degraded: {
    label: "Degraded",
    dot: "warn",
    variant: "secondary",
    pill: "border-amber-500/40 text-amber-700 dark:text-amber-400 bg-amber-500/10",
  },
  offline: {
    label: "Offline",
    dot: "error",
    variant: "destructive",
    pill: "border-destructive/40 text-destructive bg-destructive/10",
  },
  never_seen: {
    label: "No agent",
    dot: "muted",
    variant: "outline",
    pill: "border-border text-muted-foreground bg-muted",
  },
};

export function heartbeatPresentation(status: HeartbeatStatus): HeartbeatPresentation {
  return PRESENTATION[status] ?? PRESENTATION.never_seen;
}

/**
 * Format a heartbeat age (seconds) as a compact "last seen" cue:
 * ``just now`` / ``45s ago`` / ``3m ago`` / ``2h ago`` / ``5d ago``.
 * Returns ``null`` when the cluster has never been seen so callers can
 * branch on the never-seen copy.
 */
export function formatHeartbeatAge(ageSeconds: number | null): string | null {
  if (ageSeconds === null || ageSeconds === undefined) return null;
  const s = Math.max(0, Math.floor(ageSeconds));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

/**
 * One-line offline/empty-state copy for a dependent tab. ``what`` names
 * the data the tab would show ("live pods", "metrics") so the message
 * reads naturally. ``ageSeconds`` carries the last-seen cue.
 */
export function clusterOfflineMessage(status: HeartbeatStatus, ageSeconds: number | null): string {
  if (status === "never_seen") {
    return "No keep-alive agent has reported from this cluster yet. Install the agent from cluster settings to see live state.";
  }
  const age = formatHeartbeatAge(ageSeconds);
  return age ? `Cluster offline — last seen ${age}.` : "Cluster offline — no recent heartbeat.";
}
