import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { expect, it, vi } from "vitest";

import messages from "@/messages/en.json";
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { WorkloadOpsRowView } from "./ControlsSection";
import { WORKLOAD_WEB, workloadOps } from "./app-controls.fixtures";

vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => false, loading: false }),
}));

it("does not report manifest replicas as observed ready pods", () => {
  render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <PermissionsProvider value={{ granted: new Set(), loading: false }}>
        <TooltipProvider>
          <WorkloadOpsRowView {...workloadOps({ ...WORKLOAD_WEB, replicas: 3 })} />
        </TooltipProvider>
      </PermissionsProvider>
    </NextIntlClientProvider>
  );
  expect(screen.getByText("—/3 ready")).toBeVisible();
  expect(screen.queryByText("3/3 ready")).not.toBeInTheDocument();
});
