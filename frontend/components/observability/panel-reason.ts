/**
 * Observability panel reason discriminator (#1111).
 *
 * Every observability panel resolver now returns a `reason` alongside
 * its payload so the UI can render ONE honest empty-state message
 * instead of hedging ("either not configured OR the plugin doesn't
 * implement it OR no data"). This module centralises the reason type +
 * the reason -> copy mapping; each panel supplies its own nouns +
 * operator action so the message stays specific.
 *
 * Mirrors the backend enum `AstroliftObservabilityPanelReason`
 * (serialised by member NAME on the wire).
 */

export type ObservabilityPanelReason =
  | "OK"
  | "NOT_CONFIGURED"
  | "NOT_SUPPORTED_BY_PROVIDER"
  | "NO_DATA_YET"
  | "ERROR";

export interface PanelEmptyCopy {
  /** Noun for the benign NO_DATA_YET line, e.g. "DNS records". */
  thing: string;
  /**
   * Operator-facing next step for NOT_CONFIGURED, e.g.
   * "Prometheus endpoint isn't wired for this cluster".
   */
  notConfigured: string;
  /**
   * Provider label for NOT_SUPPORTED_BY_PROVIDER (only ever shown on a
   * cloud that lacks the capability), e.g. "this cloud". Defaults to a
   * generic phrasing when omitted.
   */
  provider?: string;
}

export interface PanelEmptyMessage {
  title: string;
  description: string;
}

/**
 * Map a panel `reason` + panel-specific copy to a single honest
 * empty-state message. `OK` never renders an empty state, but is
 * handled defensively.
 */
export function panelEmptyState(
  reason: ObservabilityPanelReason,
  copy: PanelEmptyCopy
): PanelEmptyMessage {
  switch (reason) {
    case "NOT_CONFIGURED":
      return {
        title: "Not configured",
        description: copy.notConfigured,
      };
    case "NOT_SUPPORTED_BY_PROVIDER":
      return {
        title: `Not available on ${copy.provider ?? "this provider"}`,
        description:
          "This cluster's provider plugin doesn't implement this read. It's supported on other clouds.",
      };
    case "ERROR":
      return {
        title: "Couldn't load",
        description: "Something went wrong loading this panel. Try again.",
      };
    case "NO_DATA_YET":
    case "OK":
    default:
      return {
        title: `No ${copy.thing} yet`,
        description: `Nothing to show for this window. ${capitalize(copy.thing)} will appear here once there's activity.`,
      };
  }
}

function capitalize(s: string): string {
  return s.length ? s[0].toUpperCase() + s.slice(1) : s;
}
