import { LIST_MY_NOTIFICATIONS } from "@/graphql/operations/operations.queries";
import { PreloadQuery } from "@/lib/apollo";

import { NotificationsClient } from "./notifications-client";

export const metadata = { title: "Notifications · Settings · Astrolift" };

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
  if (section === "alerts" || section === "install-email") return <NotificationsClient />;
  return (
    <PreloadQuery query={LIST_MY_NOTIFICATIONS} variables={{ unreadOnly: false, limit: 100 }}>
      <NotificationsClient />
    </PreloadQuery>
  );
}
