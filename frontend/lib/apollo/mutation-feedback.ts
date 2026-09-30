import { toast } from "sonner";

/** A committed mutation must keep its result even when its list refresh fails. */
export async function refetchAfterMutation(
  query: { refetch: () => Promise<unknown> },
  warningOrQueryDiff?: unknown
) {
  try {
    return await query.refetch();
  } catch {
    // Apollo also passes a cache diff as the second onQueryUpdated argument.
    toast.warning(
      typeof warningOrQueryDiff === "string"
        ? warningOrQueryDiff
        : "Could not refresh the view. Refresh to see current data."
    );
  }
}
