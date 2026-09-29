import { WebhookEventsScreen } from "@/components/screens/documentation/WebhookEventsScreen";
import { WEBHOOK_EVENTS } from "@/components/screens/documentation/webhook-events-data";

export const metadata = {
  title: "Webhook events · Documentation · Astrolift",
};

export default function WebhookEventsDocPage() {
  return <WebhookEventsScreen events={WEBHOOK_EVENTS} />;
}
