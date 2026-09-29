import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { OrgMenu } from "@/components/shell/OrgMenu";

const meta: Meta = { title: "Shell/OrgMenu" };
export default meta;
export const Named: StoryObj = { render: () => <OrgMenu name="CONFLICT" /> };
export const Loading: StoryObj = { render: () => <OrgMenu name={null} loading /> };
