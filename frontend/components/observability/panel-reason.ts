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
  /**
   * Translated title + description for the benign NO_DATA_YET state, e.g.
   * "No DNS records yet". Each panel words its own, since a noun spliced
   * into a shared sentence does not translate.
   */
  noData: PanelEmptyMessage;
  /**
   * Operator-facing next step for NOT_CONFIGURED, e.g.
   * "Prometheus endpoint isn't wired for this cluster".
   */
  notConfigured: string;
}

/** Translator over the `obsPanelReason` messages. */
export type PanelReasonTranslator = (
  key:
    | "notConfiguredTitle"
    | "notSupportedTitle"
    | "notSupportedDescription"
    | "errorTitle"
    | "errorDescription"
) => string;

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
  copy: PanelEmptyCopy,
  t: PanelReasonTranslator
): PanelEmptyMessage {
  switch (reason) {
    case "NOT_CONFIGURED":
      return {
        title: t("notConfiguredTitle"),
        description: copy.notConfigured,
      };
    case "NOT_SUPPORTED_BY_PROVIDER":
      return {
        title: t("notSupportedTitle"),
        description: t("notSupportedDescription"),
      };
    case "ERROR":
      return {
        title: t("errorTitle"),
        description: t("errorDescription"),
      };
    case "NO_DATA_YET":
    case "OK":
    default:
      return copy.noData;
  }
}
