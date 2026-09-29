import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { PodEventsPanel, type PodEventRow } from "@/components/observability/PodEventsPanel";

const meta: Meta = { title: "Patterns/Observability/PodEventsPanel" };
export default meta;

const NOW = Date.parse("2026-09-28T12:10:00Z");
const EVENTS: PodEventRow[] = [
  {
    id: "1",
    eventType: "pod.started",
    payload: { pod: "checkout-7d9f" },
    occurredAt: "2026-09-28T12:08:00Z",
  },
  {
    id: "2",
    eventType: "pod.oom_killed",
    payload: { pod: "checkout-6c1a", error: "OOMKilled" },
    occurredAt: "2026-09-28T12:05:00Z",
  },
  {
    id: "3",
    eventType: "pod.image_pull.warning",
    payload: { severity: "warning", message: "Back-off pulling image" },
    occurredAt: "2026-09-28T11:58:00Z",
  },
];

export const Default: StoryObj = {
  render: () => <PodEventsPanel appEvents={EVENTS} loading={false} nowMs={NOW} />,
};
export const Loading: StoryObj = { render: () => <PodEventsPanel appEvents={[]} loading /> };
export const Empty: StoryObj = {
  render: () => <PodEventsPanel appEvents={[]} loading={false} nowMs={NOW} />,
};
