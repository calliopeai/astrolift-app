import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import LogoutButton from "@/components/LogoutButton";

const meta: Meta = { title: "Patterns/Auth/LogoutButton" };
export default meta;
export const Default: StoryObj = { render: () => <LogoutButton /> };
