import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";
import { ModelHostingJourney } from "./ModelHostingJourney";

const meta = {
  title: "Screens/Models/ModelHostingJourney",
  component: ModelHostingJourney,
  parameters: { layout: "padded" },
  args: {
    sourceAnchor: "source",
    clusterAnchor: "cluster",
    reviewAnchor: "review",
    modelSelected: false,
    clusterSelected: false,
    readyForReview: false,
    accepted: false,
  },
} satisfies Meta<typeof ModelHostingJourney>;
export default meta;
type Story = StoryObj<typeof meta>;
export const ChooseModel: Story = {};
export const ChooseCluster: Story = { args: { modelSelected: true } };
export const ReviewChecks: Story = { args: { modelSelected: true, clusterSelected: true } };
export const ReadyForReview: Story = {
  args: { modelSelected: true, clusterSelected: true, readyForReview: true },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Ready for review")).toBeInTheDocument();
    await expect(canvas.getByRole("status")).toHaveTextContent(
      "Hosting starts after confirmation."
    );
    await expect(canvas.queryByText("Request accepted")).not.toBeInTheDocument();
  },
};
export const RequestAccepted: Story = {
  args: { modelSelected: true, clusterSelected: true, readyForReview: false, accepted: true },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByRole("status")).toHaveTextContent(
      "Track deployment readiness before connecting apps."
    );
  },
};
