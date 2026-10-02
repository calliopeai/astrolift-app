import type { AstroliftPreviewEnvironment } from "@/graphql/lifecycle/lifecycle.types";

/** Route only a live persisted FK binding; a retained hostname proves nothing. */
export function previewHasAvailableBinding(preview: AstroliftPreviewEnvironment): boolean {
  const target = preview.environment;
  return (
    preview.environmentStatus === "available" &&
    preview.status !== "torn_down" &&
    !preview.tornDownAt &&
    Boolean(
      target &&
      target.previewId === preview.id &&
      target.previewVersion === preview.version &&
      target.appSlug === preview.registeredAppSlug &&
      target.namespace === preview.namespace
    )
  );
}
