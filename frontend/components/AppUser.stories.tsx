import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import AppUser from "@/components/AppUser";

const meta: Meta = { title: "Patterns/Auth/AppUser (cut candidate)" };
export default meta;
export const Default: StoryObj = { render: () => <AppUser name="leo" /> };
