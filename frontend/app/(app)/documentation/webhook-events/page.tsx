import { WebhookEventsScreen } from "@/components/screens/documentation/WebhookEventsScreen";
import { WEBHOOK_EVENTS } from "@/components/screens/documentation/webhook-events-data";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("webhookEvents", "documentation");

export default function WebhookEventsDocPage() {
  return <WebhookEventsScreen events={WEBHOOK_EVENTS} />;
}
