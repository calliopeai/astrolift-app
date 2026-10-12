import { screen, within } from "@testing-library/react";

import { renderWithIntl as render } from "@/test/render-with-intl";
import { describe, expect, it, vi } from "vitest";

import { parseGrantParams } from "../access/access-nav";
import { AccessPresetsView } from "./AccessPresetsView";
import { ACCESS_PRESETS_FIXTURE } from "./fixtures";

vi.mock("@/components/shell/ShellHeader", () => ({
  ShellHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

describe("Easy setup (#2272)", () => {
  it("shows each preset's role per scope and what it allows and leaves out", () => {
    render(
      <AccessPresetsView
        presets={ACCESS_PRESETS_FIXTURE}
        loading={false}
        error={null}
        onRetry={() => {}}
      />
    );

    const deployer = screen.getByRole("region", { name: "Deployer" });
    expect(within(deployer).getByText("app_deployer")).toBeInTheDocument();
    expect(within(deployer).getByText("team_operator")).toBeInTheDocument();
    const deploy = within(deployer).getByRole("row", { name: /Deploy and roll back apps/ });
    expect(within(deploy).getAllByLabelText("Allowed")).toHaveLength(3);
    const approve = within(deployer).getByRole("row", { name: /Approve deploys/ });
    expect(within(approve).getAllByLabelText("Not included")).toHaveLength(3);
    expect(
      screen.getByText(/None of them makes someone an installation super-admin/)
    ).toBeInTheDocument();
  });

  it("links each scope to Grant access with that preset's role preselected", () => {
    render(
      <AccessPresetsView
        presets={ACCESS_PRESETS_FIXTURE}
        loading={false}
        error={null}
        onRetry={() => {}}
      />
    );
    const link = screen.getByRole("link", { name: "Grant Deployer at App level" });
    const url = new URL(link.getAttribute("href")!, "http://x");
    expect(url.pathname).toBe("/administration/access/grant");
    expect(url.searchParams.get("role")).toBe("app_deployer");
    expect(parseGrantParams(url.searchParams).role).toBe("app_deployer");
    expect(parseGrantParams(new URLSearchParams("role=../evil")).role).toBeNull();
  });

  it("keeps a failed read distinct from an empty catalogue", () => {
    const onRetry = vi.fn();
    render(
      <AccessPresetsView presets={null} loading={false} error="Network error" onRetry={onRetry} />
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Network error");
    screen.getByRole("button", { name: "Retry" }).click();
    expect(onRetry).toHaveBeenCalled();
  });
});
