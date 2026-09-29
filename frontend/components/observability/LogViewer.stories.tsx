import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { LogViewer } from "@/components/observability/LogViewer";
import type { AstroliftAppLogLine } from "@/graphql/lifecycle/lifecycle.types";

const meta: Meta = { title: "Patterns/Observability/LogViewer", parameters: { layout: "padded" } };
export default meta;

const LINES: AstroliftAppLogLine[] = [
  ["INFO", "pulling image registry.example.com/checkout@sha256:4f1c9e0a"],
  ["INFO", "started on :8080"],
  ["WARN", "slow query: 1.8s SELECT * FROM orders WHERE …"],
  ["ERROR", "upstream payments timed out after 30s"],
].map(([level, message], i) => ({
  container: "web",
  podName: "checkout-7d9f",
  stream: (level === "ERROR" ? "stderr" : "stdout") as AstroliftAppLogLine["stream"],
  timestamp: `2026-09-28T12:0${i}:00Z`,
  message: `${level} ${message}`,
}));

export const Default: StoryObj = {
  render: () => (
    <LogViewer
      lines={LINES}
      appSlug="checkout"
      environmentName="production"
      podName="checkout-7d9f"
    />
  ),
};
export const Loading: StoryObj = {
  render: () => <LogViewer lines={[]} appSlug="checkout" loading />,
};
export const Empty: StoryObj = {
  render: () => <LogViewer lines={[]} appSlug="checkout" emptyHint="No logs in the last hour." />,
};
