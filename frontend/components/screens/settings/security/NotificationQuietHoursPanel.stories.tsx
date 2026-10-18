import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NotificationQuietHoursPanel } from "./NotificationQuietHoursPanel";

const meta = {
  title: "Screens/Settings/Security/NotificationQuietHours",
  component: NotificationQuietHoursPanel,
  args: {
    preferences: {
      enabled: false,
      timezone: "UTC",
      startMinute: 1320,
      endMinute: 480,
      active: false,
    },
    loading: false,
    error: false,
    saving: false,
    saveError: false,
    saved: false,
    onRetry: () => {},
    onSave: async () => {},
  },
} satisfies Meta<typeof NotificationQuietHoursPanel>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Default: Story = {};
export const Active: Story = {
  args: {
    preferences: {
      enabled: true,
      timezone: "America/Costa_Rica",
      startMinute: 1320,
      endMinute: 480,
      active: true,
    },
  },
};
export const Loading: Story = { args: { loading: true, preferences: null } };
export const LoadFailed: Story = { args: { error: true, preferences: null } };
export const Saving: Story = { args: { saving: true } };
export const SaveFailed: Story = { args: { saveError: true } };
export const Saved: Story = { args: { saved: true } };
export const Width768: Story = {
  decorators: [
    (Story) => (
      <div style={{ width: 768 }}>
        <Story />
      </div>
    ),
  ],
};
