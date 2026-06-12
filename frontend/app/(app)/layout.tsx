import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/AppSidebar";
import { CommandPalette } from "@/components/CommandPalette";
import { KeyboardShortcuts } from "@/components/KeyboardShortcuts";
import { LiveRegionProvider } from "@/components/LiveRegion";
import { PlatformIncidentBanner } from "@/components/PlatformIncidentBanner";
import { ScmCallbackToast } from "@/components/ScmCallbackToast";
import { SessionExpiredModal } from "@/components/SessionExpiredModal";
import { SkipToContent } from "@/components/SkipToContent";
import { StepUpPrompt } from "@/components/StepUpPrompt";
import { getClient } from "@/lib/apollo";
import { GET_ME } from "@/graphql/user/user.queries";
import { GET_MY_PERMISSIONS } from "@/graphql/permissions/astrolift.queries";
import { LIST_ORGANIZATIONS } from "@/graphql/identity/identity.queries";
import type { CurrentUser, MeQueryData, MeQueryVariables } from "@/graphql/user/user.types";
import { PageHeader } from "@/components/PageHeader";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  // Check for auth token in cookies — if no token at all, redirect to login
  const cookieStore = await cookies();
  const hasToken = cookieStore.has("backend_jwt") || cookieStore.has("sessionid");

  if (!hasToken) {
    redirect("/auth/login");
  }

  let ssrUser: CurrentUser | null = null;

  try {
    const client = await getClient();
    // Fetch all three identity queries in parallel (#822).
    // Previously nested PreloadQuery components created a sequential
    // waterfall (GET_ME → GET_MY_PERMISSIONS → LIST_ORGANIZATIONS) that
    // caused a 1-2s blank screen before the sidebar and page content
    // rendered. Promise.all reduces this to one round-trip time. getClient()
    // serialises all results into the Apollo SSR cache automatically, so
    // client-side useQuery hooks find data immediately (no loading flash).
    const [meResult] = await Promise.all([
      client.query<MeQueryData, MeQueryVariables>({ query: GET_ME }),
      client.query({ query: GET_MY_PERMISSIONS }),
      client.query({ query: LIST_ORGANIZATIONS }),
    ]);
    ssrUser = meResult.data?.me ?? null;
  } catch {
    // network error or invalid token — don't redirect here,
    // let client-side Apollo errorLink handle UNAUTHENTICATED
  }

  return (
    <LiveRegionProvider>
      <SkipToContent />
      <div className="flex min-h-svh flex-col">
        <PlatformIncidentBanner />
        <SidebarProvider className="flex-1">
          <AppSidebar ssrUser={ssrUser} />
          <SidebarInset className="overflow-x-hidden">
            <PageHeader />
            <main
              id="main-content"
              tabIndex={-1}
              className="flex flex-1 flex-col outline-none"
            >
              {children}
            </main>
          </SidebarInset>
          <CommandPalette />
          <KeyboardShortcuts />
          <ScmCallbackToast />
          <SessionExpiredModal />
          <StepUpPrompt />
        </SidebarProvider>
      </div>
    </LiveRegionProvider>
  );
}
