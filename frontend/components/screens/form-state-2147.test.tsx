import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import messages from "@/messages/en.json";
import { NEW_POLICY } from "./administration/access/fixtures";
import { PolicyEditorScreen } from "./administration/access/PolicyEditorScreen";
import { newRoleProps } from "./administration/permissions/fixtures";
import { NewRoleScreen } from "./administration/permissions/NewRoleScreen";
import { SKILL_BUILDER } from "./agents/skills/agent-skills.fixtures";
import { SkillBuilderScreen } from "./agents/skills/SkillBuilderScreen";
import { DETAIL } from "./agents/tools/agent-tools.fixtures";
import { ToolDetailScreen } from "./agents/tools/ToolDetailScreen";
import { DEPLOY_STRATEGY } from "./apps/overview/app-overview-cards-b.fixtures";
import { DeployStrategyCardView } from "./apps/overview/DeployStrategyCard";
import { ScalePopoverView } from "./apps/workloads/ScalePopover";
import { BootstrapPlanView } from "./clusters/settings/BootstrapPlan";
import { PLAN } from "./clusters/settings/fixtures";
import { DeployModelScreen } from "./models/DeployModelScreen";
import { DEPLOY } from "./models/models-providers.fixtures";
import { ProjectResourcesScreen } from "./projects/ProjectResourcesScreen";
import { RESOURCES } from "./projects/projects-detail.fixtures";
import { ConnectGitLabDialogView } from "./settings/source-providers/ConnectGitLabDialog";
import { ConnectSourceDialogView } from "./settings/source-providers/ConnectSourceDialog";
import {
  CONNECT_GITLAB_PROPS,
  CONNECT_SOURCE_PROPS,
} from "./settings/source-providers/settings-source-connect.fixtures";

const navigation = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => navigation,
  usePathname: () => "/apps",
  useSearchParams: () => new URLSearchParams(),
  useSelectedLayoutSegment: () => "builder",
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true, loading: false }),
}));
function wrapper({ children }: PropsWithChildren) {
  return (
    <NextIntlClientProvider locale="en" messages={messages}>
      {children}
    </NextIntlClientProvider>
  );
}
beforeEach(() => vi.clearAllMocks());

describe("editor draft and save state", () => {
  it("keeps a tool draft after save while the cached record is still old, then accepts the refreshed normalization", async () => {
    const save = vi.fn().mockResolvedValue({});
    const { rerender } = render(<ToolDetailScreen {...DETAIL} save={save} />, { wrapper });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Edited Tool" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save tool" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save tool" })).toBeDisabled());
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Edited Tool");
    rerender(
      <ToolDetailScreen {...DETAIL} save={save} tool={{ ...DETAIL.tool!, name: "edited tool" }} />
    );
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("edited tool");
  });
  it("keeps a second tool edit when the first save's refetch arrives", async () => {
    const { rerender } = render(<ToolDetailScreen {...DETAIL} save={async () => ({})} />, {
      wrapper,
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "First edit" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save tool" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save tool" })).toBeDisabled());
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Second edit" },
    });
    rerender(<ToolDetailScreen {...DETAIL} tool={{ ...DETAIL.tool!, name: "First edit" }} />);
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Second edit");
  });
  it("clears a tool draft when navigating to a different tool", () => {
    const { rerender } = render(<ToolDetailScreen {...DETAIL} />, { wrapper });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Unsaved" },
    });
    rerender(
      <ToolDetailScreen
        {...DETAIL}
        tool={{ ...DETAIL.tool!, id: "other-tool", name: "Other tool" }}
      />
    );
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Other tool");
    expect(screen.getByRole("button", { name: "Save tool" })).toBeDisabled();
  });
  it("keeps saved skill fields until its refetch lands, without overwriting another edit", async () => {
    const saveSkill = vi.fn().mockResolvedValue({});
    const { rerender } = render(<SkillBuilderScreen {...SKILL_BUILDER} saveSkill={saveSkill} />, {
      wrapper,
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Updated skill" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save skill" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save skill" })).toBeDisabled());
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Updated skill");
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Another edit" },
    });
    rerender(
      <SkillBuilderScreen
        {...SKILL_BUILDER}
        skill={{ ...SKILL_BUILDER.skill!, name: "Updated skill" }}
      />
    );
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Another edit");
  });
  it("retains a refused skill draft for retry", async () => {
    render(
      <SkillBuilderScreen {...SKILL_BUILDER} saveSkill={async () => ({ form: "Offline" })} />,
      { wrapper }
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Retry this" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save skill" }));
    await waitFor(() => expect(screen.getByText("Offline")).toBeInTheDocument());
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Retry this");
    expect(screen.getByRole("button", { name: "Save skill" })).toBeEnabled();
  });
});

describe("bootstrap recipe refresh", () => {
  it("retains component and option overrides across a fresh plan reference", () => {
    const onInstall = vi.fn();
    const { rerender } = render(<BootstrapPlanView {...PLAN} onInstall={onInstall} />, { wrapper });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "letsencrypt-staging" } });
    const wasChecked = (screen.getAllByRole("checkbox")[1] as HTMLInputElement).checked;
    fireEvent.click(screen.getAllByRole("checkbox")[1]);
    rerender(
      <BootstrapPlanView {...PLAN} onInstall={onInstall} plan={structuredClone(PLAN.plan)} />
    );
    expect(screen.getByRole("combobox")).toHaveValue("letsencrypt-staging");
    fireEvent.click(screen.getByRole("button", { name: /Install/ }));
    expect(onInstall.mock.calls[0][1].cert_manager.issuer).toBe("letsencrypt-staging");
    expect(onInstall.mock.calls[0][0].aws_lb_controller).toBe(!wasChecked);
  });
  it("resets recipe overrides when the cluster changes", () => {
    const { rerender } = render(<BootstrapPlanView {...PLAN} />, { wrapper });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "letsencrypt-staging" } });
    rerender(<BootstrapPlanView {...PLAN} plan={{ ...PLAN.plan!, clusterId: "other-cluster" }} />);
    expect(screen.getByRole("combobox")).toHaveValue("letsencrypt-prod");
  });
});

describe("dialog edit sessions", () => {
  it("seeds strategy from refreshed values on reopen while keeping failed edits during refetch", async () => {
    const onSave = vi.fn().mockResolvedValue(false);
    const { rerender } = render(
      <DeployStrategyCardView {...DEPLOY_STRATEGY} onSave={onSave} defaultOpen />,
      { wrapper }
    );
    const input = screen.getByRole("textbox", { name: /^Deploy branch/ });
    fireEvent.change(input, { target: { value: "draft-branch" } });
    fireEvent.click(screen.getByRole("button", { name: /Save/ }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    rerender(
      <DeployStrategyCardView
        {...DEPLOY_STRATEGY}
        onSave={onSave}
        app={{ ...DEPLOY_STRATEGY.app, deployBranch: "latest-server-branch" }}
      />
    );
    expect(screen.getByRole("textbox", { name: /^Deploy branch/ })).toHaveValue("draft-branch");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByRole("textbox", { name: /^Deploy branch/ })).toHaveValue(
      "latest-server-branch"
    );
  });
  it("preserves a staged scale across live replicas updates and resets only on reopening", () => {
    const props = {
      workloadName: "api",
      currentDesired: 2,
      loading: false,
      apply: async () => false,
    };
    const { rerender } = render(<ScalePopoverView {...props} defaultOpen />, { wrapper });
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "7" } });
    rerender(<ScalePopoverView {...props} currentDesired={4} />);
    expect(screen.getByRole("spinbutton")).toHaveValue(7);
    fireEvent.keyDown(screen.getByRole("spinbutton"), { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Scale" }));
    expect(screen.getByRole("spinbutton")).toHaveValue(4);
  });
  it.each(["source", "gitlab"])(
    "clears %s credentials on programmatic close and reopen",
    (kind) => {
      const component = (open: boolean) =>
        kind === "source" ? (
          <ConnectSourceDialogView {...CONNECT_SOURCE_PROPS} open={open} />
        ) : (
          <ConnectGitLabDialogView {...CONNECT_GITLAB_PROPS} open={open} />
        );
      const { rerender } = render(component(true), { wrapper });
      const label = kind === "source" ? "Display name (optional)" : "Application ID";
      fireEvent.change(screen.getByLabelText(label), { target: { value: "prior-credential" } });
      rerender(component(false));
      expect(screen.queryByLabelText(label)).not.toBeInTheDocument();
      rerender(component(true));
      expect(screen.getByLabelText(label)).toHaveValue("");
    }
  );
});

describe("redesigned pages retain and reset form state", () => {
  it("keeps the new role draft when the catalog refetches and resets on remount", () => {
    const { rerender, unmount } = render(<NewRoleScreen {...newRoleProps()} />, { wrapper });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Custom role" },
    });
    rerender(<NewRoleScreen {...newRoleProps({ roles: [...newRoleProps().roles] })} />);
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Custom role");
    unmount();
    render(<NewRoleScreen {...newRoleProps()} />, { wrapper });
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("");
  });
  it("keeps a policy draft through loading updates and resets on remount", () => {
    const { rerender, unmount } = render(<PolicyEditorScreen {...NEW_POLICY} />, { wrapper });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Custom policy" },
    });
    rerender(<PolicyEditorScreen {...NEW_POLICY} />);
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Custom policy");
    unmount();
    render(<PolicyEditorScreen {...NEW_POLICY} />, { wrapper });
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("");
  });
  it("retains model GPU choices for a refused deploy, and resets the page on remount", async () => {
    const deploy = vi.fn().mockResolvedValue("Capacity refused");
    const { unmount } = render(
      <DeployModelScreen
        {...DEPLOY}
        initialStep={3}
        initialEnvId={DEPLOY.envs[0].id}
        deploy={deploy}
      />,
      { wrapper }
    );
    fireEvent.click(screen.getByRole("button", { name: /Deploy/ }));
    await waitFor(() => expect(screen.getByText("Capacity refused")).toBeInTheDocument());
    unmount();
    render(<DeployModelScreen {...DEPLOY} />, { wrapper });
    expect(screen.queryByText("Capacity refused")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument();
  });
});

describe("bundle naming", () => {
  it("updates an untouched slug for the whole name, then respects an explicit slug", () => {
    render(<ProjectResourcesScreen {...RESOURCES} />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: /New.*bundle/i }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "J" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Jira" } });
    expect(screen.getByLabelText("Slug")).toHaveValue("jira");
    fireEvent.change(screen.getByLabelText("Slug"), { target: { value: "manual-slug" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Linear" } });
    expect(screen.getByLabelText("Slug")).toHaveValue("manual-slug");
  });
});

describe("edits made while a save is pending", () => {
  it("keeps a newer tool edit dirty after the earlier save completes", async () => {
    let finish!: (errors: Record<string, string>) => void;
    const save = vi.fn(
      () =>
        new Promise<Record<string, string>>((resolve) => {
          finish = resolve;
        })
    );
    const { rerender } = render(<ToolDetailScreen {...DETAIL} save={save} />, { wrapper });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "First edit" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save tool" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Newer edit" },
    });
    rerender(
      <ToolDetailScreen {...DETAIL} save={save} tool={{ ...DETAIL.tool!, name: "First edit" }} />
    );
    await act(async () => finish({}));
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Newer edit");
    expect(screen.getByRole("button", { name: "Save tool" })).toBeEnabled();
  });
  it("adopts tool normalization which arrived before the save settled", async () => {
    let finish!: (errors: Record<string, string>) => void;
    const save = vi.fn(
      () =>
        new Promise<Record<string, string>>((resolve) => {
          finish = resolve;
        })
    );
    const { rerender } = render(<ToolDetailScreen {...DETAIL} save={save} />, { wrapper });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: " Name to normalize " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save tool" }));
    rerender(
      <ToolDetailScreen
        {...DETAIL}
        save={save}
        tool={{ ...DETAIL.tool!, name: "Name to normalize" }}
      />
    );
    await act(async () => finish({}));
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Name to normalize");
    expect(screen.getByRole("button", { name: "Save tool" })).toBeDisabled();
  });
  it("a clone of unchanged cached tool data does not undo an accepted edit", async () => {
    const save = vi.fn().mockResolvedValue({});
    const { rerender } = render(<ToolDetailScreen {...DETAIL} save={save} />, { wrapper });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Accepted edit" },
    });
    rerender(<ToolDetailScreen {...DETAIL} save={save} tool={{ ...DETAIL.tool! }} />);
    fireEvent.click(screen.getByRole("button", { name: "Save tool" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save tool" })).toBeDisabled());
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Accepted edit");
  });
  it("keeps newer skill instructions dirty when its earlier save settles", async () => {
    let finish!: (errors: Record<string, string>) => void;
    const saveSkill = vi.fn(
      () =>
        new Promise<Record<string, string>>((resolve) => {
          finish = resolve;
        })
    );
    render(<SkillBuilderScreen {...SKILL_BUILDER} saveSkill={saveSkill} />, { wrapper });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "First skill edit" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save skill" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Later skill edit" },
    });
    await act(async () => finish({}));
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Later skill edit");
    expect(screen.getByRole("button", { name: "Save skill" })).toBeEnabled();
  });
});
