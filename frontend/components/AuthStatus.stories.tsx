import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import AuthStatus from "@/components/AuthStatus";

const meta: Meta = { title: "Patterns/Auth/AuthStatus (cut candidate)" };
export default meta;
export const SignedIn: StoryObj = { render: () => <AuthStatus loading={false} username="leo" /> };
export const SignedOut: StoryObj = { render: () => <AuthStatus loading={false} username={null} /> };
export const Loading: StoryObj = { render: () => <AuthStatus loading username={null} /> };
