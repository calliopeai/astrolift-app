import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";

const meta: Meta<typeof Section> = { title: "Atoms/Section", component: Section };
export default meta;

export const Default: StoryObj = {
  render: () => (
    <Section
      title="Central auth"
      description="Sign-in for every app on this cluster, through the install's identity provider."
      action={<Button size="sm">Save</Button>}
      divided
    >
      <p className="text-sm">Section content.</p>
    </Section>
  ),
};
