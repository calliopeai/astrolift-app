/** Shared by server preloads and the client poll; this module is server-safe. */
export function podEventsVariables(appSlug: string) {
  return { limit: 100, appSlug };
}
