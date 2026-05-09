import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/AppSidebar";
import { CommandPalette } from "@/components/CommandPalette";
import { LiveRegionProvider } from "@/components/LiveRegion";
import { SkipToContent } from "@/components/SkipToContent";
import { PreloadQuery, getClient } from "@/lib/apollo";
import { GET_ME } from "@/graphql/user/user.queries";
import { GET_MY_PERMISSIONS } from "@/graphql/permissions/astrolift.queries";
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
    const { data } = await client.query<MeQueryData, MeQueryVariables>({ query: GET_ME });
    ssrUser = data?.me ?? null;
  } catch {
    // network error or invalid token — don't redirect here,
    // let client-side Apollo errorLink handle UNAUTHENTICATED
  }

  return (
    <PreloadQuery query={GET_ME}>
      <PreloadQuery query={GET_MY_PERMISSIONS}>
        <LiveRegionProvider>
          <SkipToContent />
          <SidebarProvider>
            <AppSidebar ssrUser={ssrUser} />
            <SidebarInset>
              <PageHeader />
              <main id="main-content" tabIndex={-1} className="flex flex-1 flex-col outline-none">
                {children}
              </main>
            </SidebarInset>
            <CommandPalette />
          </SidebarProvider>
        </LiveRegionProvider>
      </PreloadQuery>
    </PreloadQuery>
  );
}
