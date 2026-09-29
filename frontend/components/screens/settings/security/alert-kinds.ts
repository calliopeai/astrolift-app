// The alert-kind axis the UI exposes. Server-side accepts arbitrary
// strings; this list is the operator-facing subset surfaced as columns
// in the matrix. New kinds added server-side need a label here before
// they render.
export const ALERT_KINDS: { value: string; label: string }[] = [
  { value: "deploy_success", label: "Deploy success" },
  { value: "deploy_failure", label: "Deploy failure" },
  { value: "error_spike", label: "Error spike" },
  { value: "preview_created", label: "Preview created" },
  { value: "preview_destroyed", label: "Preview destroyed" },
];

// Map keyed by ``{appSlug}|{alertKind}`` so cell lookups are O(1).
export function subKey(appSlug: string, alertKind: string) {
  return `${appSlug}|${alertKind}`;
}
