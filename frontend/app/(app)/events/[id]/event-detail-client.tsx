"use client";

import { EventDetail } from "@/components/screens/events/EventDetail";
import { useEventDetail } from "@/components/screens/events/use-events";

export function EventDetailClient({ id }: { id: string }) {
  return <EventDetail {...useEventDetail(id)} />;
}
