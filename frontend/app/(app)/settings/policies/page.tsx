import { redirect } from "next/navigation";

export const metadata = { title: "Policies · Astrolift" };

/**
 * Policies moved to the Admin control plane at
 * /administration/policies. Server-side redirect keeps old
 * deep-links working without a client-side bounce.
 */
export default function SettingsPoliciesAliasPage() {
  redirect("/administration/policies");
}
