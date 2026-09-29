import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { BareShell } from "@/components/screens/shell/BareShell";

/**
 * Chromeless shell for surfaces that are the whole window rather than a page
 * in the app: today just the popped-out pod terminal (#1246). No sidebar, no
 * page header, no tab bar — an OS window sized to a terminal should be all
 * terminal.
 *
 * The auth gate matches `(app)`: no token cookie at all means there is
 * nothing to render, so bounce to login rather than opening a window that
 * only ever shows a denied banner. Anything past that (stale token, missing
 * exec capability) surfaces as the terminal's own 4401/4403 close states.
 */
export default async function BareLayout({ children }: { children: React.ReactNode }) {
  const cookieStore = await cookies();
  if (!cookieStore.has("backend_jwt") && !cookieStore.has("sessionid")) {
    redirect("/auth/login");
  }
  return <BareShell>{children}</BareShell>;
}
