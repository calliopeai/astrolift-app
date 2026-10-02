import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { VncViewer } from "@/components/observability/VncViewer";

/**
 * A device component: the RFB session over the VNC relay is the component, so
 * it has no data props to fake. In the catalog there is no relay, so this
 * shows the real connecting and disconnected chrome around the framebuffer.
 */
const meta: Meta<typeof VncViewer> = {
  title: "Patterns/Observability/VncViewer",
  component: VncViewer,
  args: { vncPath: "/app/vnc/7f3c2a10-4d5e-4f60-9a1b-2c3d4e5f6a7b", className: "h-[24rem]" },
};
export default meta;

export const NoRelay: StoryObj<typeof VncViewer> = {};

export const FrenchNoRelay: StoryObj<typeof VncViewer> = {
  render: (args) => (
    <NextIntlClientProvider locale="fr" messages={fr}>
      <VncViewer {...args} />
    </NextIntlClientProvider>
  ),
};
export const JapaneseNoRelay: StoryObj<typeof VncViewer> = {
  render: (args) => (
    <NextIntlClientProvider locale="ja" messages={ja}>
      <VncViewer {...args} />
    </NextIntlClientProvider>
  ),
};
