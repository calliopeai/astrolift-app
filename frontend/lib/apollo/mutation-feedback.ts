import { toast } from "sonner";

/** A committed mutation must keep its result even when its list refresh fails. */
export async function refetchAfterMutation(query: { refetch: () => Promise<unknown> }) {
  try {
    return await query.refetch();
  } catch {
    toast.warning("Could not refresh the view. Refresh to see current data.");
  }
}
