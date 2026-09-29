import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { StaleManifestNotice } from "@/components/StaleManifestNotice";

const meta: Meta = { title: "Patterns/Notices/StaleManifestNotice" };
export default meta;

export const States: StoryObj = {
  render: () => (
    <div className="flex max-w-2xl flex-col gap-3">
      <StaleManifestNotice status="diverged" error={null} appSlug="checkout" />
      <StaleManifestNotice
        status="fetch_failed"
        error="404 astrolift.toml not found on main"
        appSlug="checkout"
      />
      <StaleManifestNotice
        status="parse_failed"
        error="line 12: unknown key [ingress.acess]"
        appSlug="checkout"
        context="registration"
      />
    </div>
  ),
};
