import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NvkeSpireServerForm } from "./NvkeSpireServerForm";
export default {
  title: "Screens/NVKE/SPIRE server",
  component: NvkeSpireServerForm,
  args: {
    parametersText: '{"body":{"address":"spire.example.test","port":30440}}',
    timeoutSeconds: 7200,
    recovery: false,
    pending: false,
    disabled: false,
    onReview: () => {},
  },
} satisfies Meta<typeof NvkeSpireServerForm>;
type Story = StoryObj<typeof NvkeSpireServerForm>;
export const Configured: Story = {};
export const Initial: Story = { args: { parametersText: '{"body":{}}' } };
export const Recovery: Story = { args: { recovery: true } };
export const Pending: Story = { args: { pending: true } };
export const ReadOnly: Story = { args: { disabled: true } };
export const LongStrings: Story = {
  args: {
    parametersText: JSON.stringify({ body: { address: "spire-server-".repeat(50), port: 30440 } }),
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeSpireServerForm {...args} />
    </div>
  ),
};
