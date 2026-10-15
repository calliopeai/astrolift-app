import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithIntl as render } from "@/test/render-with-intl";

import { AccessPresetsView, MODEL_CONNECTION_POLICY_HREF } from "./AccessPresetsView";
import { DeployApprovalSetup } from "./DeployApprovalSetup";
import { DEPLOY_APPROVAL_FIXTURE } from "./fixtures";
import { deployApprovalInput } from "./use-deploy-approval-setup";

vi.mock("@/components/shell/ShellHeader", () => ({
  ShellHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const [CHECKOUT, BILLING, LEDGER] = DEPLOY_APPROVAL_FIXTURE.apps!;

async function pick(label: string, option: string) {
  fireEvent.click(screen.getByRole("combobox", { name: label }));
  fireEvent.click(await screen.findByRole("option", { name: option }));
}

describe("Guided deploy approval (#2272)", () => {
  it("says it is the whole app's setting and writes approval, a team and a count", async () => {
    const onSave = vi.fn(async () => null);
    render(<DeployApprovalSetup {...DEPLOY_APPROVAL_FIXTURE} onSave={onSave} />);
    await pick("App", "checkout");
    expect(
      screen.getByText(/applies to every deploy of checkout, by anyone who can deploy it/)
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: "Require approval before each deploy" }));
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled(); // no approver team yet
    await pick("Approver team", "Payments");
    await pick("Approvals needed", "2 approvals");
    fireEvent.click(save);

    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(CHECKOUT, {
        requiresApproval: true,
        minimumApprovals: 2,
        approverTeamId: "team-payments",
      })
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Saved.");
  });

  it("turns approval off and reports a refused save in place", async () => {
    const onSave = vi.fn(async () => "version mismatch");
    render(
      <DeployApprovalSetup
        {...DEPLOY_APPROVAL_FIXTURE}
        initialAppId={BILLING!.id}
        onSave={onSave}
      />
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "Require approval before each deploy" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(
        BILLING,
        expect.objectContaining({ requiresApproval: false })
      )
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("version mismatch");
  });

  it("is read-only without permission to update the app", () => {
    render(<DeployApprovalSetup {...DEPLOY_APPROVAL_FIXTURE} initialAppId={LEDGER!.id} />);
    expect(screen.getByRole("note")).toHaveTextContent(
      "changing ledger's settings needs permission to update the app"
    );
    expect(
      screen.getByRole("checkbox", { name: "Require approval before each deploy" })
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("sends only the deploy-approval fields, against the version read", () => {
    expect(
      deployApprovalInput(BILLING!, {
        requiresApproval: false,
        minimumApprovals: 2,
        approverTeamId: null,
      })
    ).toEqual({
      id: "app-billing",
      requiresApproval: false,
      ifMatchVersion: 9,
    });
    expect(
      deployApprovalInput(CHECKOUT!, {
        requiresApproval: true,
        minimumApprovals: 3,
        approverTeamId: "team-platform",
      })
    ).toEqual({
      id: "app-checkout",
      requiresApproval: true,
      approverTeamId: "team-platform",
      minimumApprovals: 3,
      ifMatchVersion: 4,
    });
    // A team replaces specific approver people: the backend refuses both at once.
    expect(
      deployApprovalInput(LEDGER!, {
        requiresApproval: true,
        minimumApprovals: 1,
        approverTeamId: "team-payments",
      })
    ).toMatchObject({ approverUserIds: [], approverTeamId: "team-payments" });
  });

  it("keeps a failed read distinct from an org with no apps", () => {
    const onRetry = vi.fn();
    const { unmount } = render(
      <DeployApprovalSetup
        {...DEPLOY_APPROVAL_FIXTURE}
        apps={null}
        error="boom"
        onRetry={onRetry}
      />
    );
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalled();
    unmount();
    render(<DeployApprovalSetup {...DEPLOY_APPROVAL_FIXTURE} apps={[]} />);
    expect(screen.getByText("This organization has no apps yet.")).toBeInTheDocument();
  });
});

describe("Easy setup: model access (#2272)", () => {
  it("has no Model consumer preset and links to the org's model-connection policy", () => {
    render(<AccessPresetsView presets={[]} loading={false} error={null} onRetry={() => {}} />);
    expect(screen.queryByText(/Model consumer/)).not.toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Open the model connection policy" });
    expect(link).toHaveAttribute("href", MODEL_CONNECTION_POLICY_HREF);
    expect(MODEL_CONNECTION_POLICY_HREF).toBe(
      "/administration/organization?section=model-connections"
    );
  });
});
