import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { TelemetryExplorerScreen } from "./TelemetryExplorerScreen";
import { useExplorerFixture } from "./explorer-fixtures";
import type { ExplorerProps } from "./use-scoped-explorer";
const meta: Meta = {
  title: "Screens/Telemetry/ScopedExplorers",
  excludeStories: ["ExplorerStory"],
  parameters: { layout: "fullscreen" },
};
export default meta;
export function ExplorerStory({
  mode,
  overrides = {},
}: {
  mode: "logs" | "traces";
  overrides?: Partial<ExplorerProps>;
}) {
  const props = useExplorerFixture(mode);
  return <TelemetryExplorerScreen {...props} {...overrides} />;
}
export const HistoricalUnavailable: StoryObj = { render: () => <ExplorerStory mode="logs" /> };
export const TracesUnavailable: StoryObj = { render: () => <ExplorerStory mode="traces" /> };
export const ChooseApp: StoryObj = {
  render: () => <ExplorerStory mode="logs" overrides={{ app: null, environment: null }} />,
};
export const Loading: StoryObj = {
  render: () => <ExplorerStory mode="traces" overrides={{ loading: true, traces: null }} />,
};
export const Empty: StoryObj = {
  render: () => (
    <ExplorerStory
      mode="traces"
      overrides={{ traces: { reason: "NO_DATA_YET", items: [], scope: null, truncated: false } }}
    />
  ),
};
export const Error: StoryObj = {
  render: () => (
    <ExplorerStory
      mode="logs"
      overrides={{ error: "Recorded backend diagnostic: connection refused" }}
    />
  ),
};
export const PlacementUnavailable: StoryObj = {
  render: () => (
    <ExplorerStory
      mode="logs"
      overrides={{
        logs: {
          reason: "NOT_CONFIGURED",
          items: [],
          scope: null,
          nextCursor: null,
          historicalAvailable: false,
          reachedRetention: false,
        },
      }}
    />
  ),
};
export const BoundedTraces: StoryObj = {
  render: () => (
    <ExplorerStory
      mode="traces"
      overrides={{
        traces: {
          reason: "OK",
          scope: null,
          truncated: true,
          items: [
            {
              traceId: "a".repeat(32),
              rootService: "example-service",
              rootOperation: "GET /health",
              durationMs: 4,
              spanCount: 1,
              statusCode: "OK",
            },
          ],
        },
      }}
    />
  ),
};
