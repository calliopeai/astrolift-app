import { LIST_MY_NOTIFICATIONS } from "@/graphql/operations/operations.queries";
import { PreloadQuery } from "@/lib/apollo";

import { NotificationsClient } from "./notifications-client";

export const metadata = { title: "Notifications · Settings · Astrolift" };

export default function NotificationsPage() {
  return (
    <PreloadQuery
      query={LIST_MY_NOTIFICATIONS}
      variables={{ unreadOnly: false, limit: 100 }}
    >
      <NotificationsClient />
    </PreloadQuery>
  );
}
