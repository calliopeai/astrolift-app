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

/** No preview with this id in the list window, or no permission to see it. */
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
