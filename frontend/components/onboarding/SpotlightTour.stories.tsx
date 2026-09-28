import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { SpotlightTour } from "@/components/onboarding/SpotlightTour";

const meta: Meta = { title: "Screens/Onboarding/SpotlightTour" };
export default meta;

export const Open: StoryObj = {
  render: () => (
    <div>
      <p className="text-muted-foreground text-sm">The tour opens over the page.</p>
      <SpotlightTour open onOpenChange={() => {}} onDismiss={() => {}} />
    </div>
  ),
};
