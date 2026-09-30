import { toast } from "sonner";

export async function copyWithFeedback(text: string, success: string, failure: string) {
  try {
    if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
    await navigator.clipboard.writeText(text);
    toast.success(success);
    return true;
  } catch {
    toast.error(failure);
    return false;
  }
}
