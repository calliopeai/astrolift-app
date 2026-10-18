import { LIST_MY_NOTIFICATIONS } from "@/graphql/operations/operations.queries";
import { PreloadQuery } from "@/lib/apollo";
import { routeTitle } from "@/lib/i18n/route-title";

import { NotificationsClient } from "./notifications-client";

export const generateMetadata = routeTitle("notifications", "settings");

/**
 * Preloads the inbox only when the inbox is the section on screen (the
 * default): a hidden section does not fetch (list rule 2).
 */
export default async function NotificationsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { section } = await searchParams;
  if (
    section === "alerts" ||
    section === "quiet-hours" ||
    section === "install-email" ||
    section === "workflow-delivery"
  )
    return <NotificationsClient />;
  return (
    <PreloadQuery query={LIST_MY_NOTIFICATIONS} variables={{ unreadOnly: false, limit: 100 }}>
      <NotificationsClient />
    </PreloadQuery>
  );
}
