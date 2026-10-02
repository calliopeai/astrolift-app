import { NextIntlClientProvider } from "next-intl";
import german from "@/messages/de.json";

import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG_PREVIEW, PREVIEWS, previewDetailProps } from "./previews.fixtures";
import { PreviewDetailScreen } from "./PreviewDetail";

const meta: Meta = {
  title: "Screens/Previews/PreviewDetail",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <PreviewDetailScreen {...previewDetailProps()} /> };

export const Loading: Story = {
  render: () => <PreviewDetailScreen {...previewDetailProps({ preview: null, loading: true })} />,
};

/** Exact GUID missing, deleted, or not visible to this viewer. */
export const NotFound: Story = {
  render: () => <PreviewDetailScreen {...previewDetailProps({ preview: null })} />,
};

export const ErrorState: Story = {
  render: () => (
    <PreviewDetailScreen
      {...previewDetailProps({ preview: null, error: { message: "upstream timed out" } })}
    />
  ),
};

export const RuntimeNotRequested: Story = {
  render: () => (
    <PreviewDetailScreen
      {...previewDetailProps({ preview: { ...PREVIEWS[0], runtimeStatus: "not_requested" } })}
    />
  ),
};

export const RuntimeUnavailable: Story = {
  render: () => (
    <PreviewDetailScreen
      {...previewDetailProps({
        preview: { ...PREVIEWS[0], runtimeStatus: "unavailable", estimatedDailyCostUsd: null },
      })}
    />
  ),
};

export const RetiredBinding: Story = {
  render: () => (
    <PreviewDetailScreen
      {...previewDetailProps({ preview: { ...PREVIEWS[0], environmentStatus: "retired" } })}
    />
  ),
};

export const ExactLogsLoaded: Story = {
  render: () => (
    <PreviewDetailScreen
      {...previewDetailProps({
        logsRequested: true,
        logs: {
          reason: "OK",
          historicalAvailable: true,
          items: [
            {
              timestamp: "2026-10-02T01:00:00Z",
              message: "Preview application ready",
              level: "info",
              podName: "canonical-preview-web",
              container: "web",
            },
          ],
        },
      })}
    />
  ),
};

/** Failed: the failure first, with the way to its logs; approximate cost with caveats. */
export const Failed: Story = {
  render: () => <PreviewDetailScreen {...previewDetailProps({ preview: PREVIEWS[3] })} />,
};

/** Building: no hostname link yet, no resources. */
export const Empty: Story = {
  render: () => <PreviewDetailScreen {...previewDetailProps({ preview: PREVIEWS[1] })} />,
};

/** Torn down: no PR link, no source, no cost. */
export const TornDown: Story = {
  render: () => <PreviewDetailScreen {...previewDetailProps({ preview: PREVIEWS[2] })} />,
};

export const LongStrings: Story = {
  render: () => <PreviewDetailScreen {...previewDetailProps({ preview: LONG_PREVIEW })} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <PreviewDetailScreen {...previewDetailProps({ preview: LONG_PREVIEW })} />
    </div>
  ),
};

export const GermanWidth768: Story = {
  render: () => (
    <NextIntlClientProvider locale="de" messages={german}>
      <div style={{ width: 768 }}>
        <PreviewDetailScreen {...previewDetailProps({ preview: LONG_PREVIEW })} />
      </div>
    </NextIntlClientProvider>
  ),
};
