import { MockedProvider } from "@apollo/client/testing/react";
import { readFileSync } from "node:fs";
import { buildSchema, validate } from "graphql";
import type { MockedResponse } from "@apollo/client/testing";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CREATE_PIPELINE } from "@/graphql/pipelines/pipelines.mutations";
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import { NewPipelineScreen } from "./NewPipelineScreen";
import { useNewPipeline } from "./use-new-pipeline";

import { renderWithIntl as render } from "@/test/render-with-intl";

const mocks = vi.hoisted(() => ({ push: vi.fn(), success: vi.fn(), warning: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("sonner", () => ({ toast: { success: mocks.success, warning: mocks.warning } }));
const input = {
  name: "Build",
  repoUrl: "git@github.com:acme/build.git",
  defaultBranch: "main",
  tomlPath: "ci/build.toml",
};
const request = { query: CREATE_PIPELINE, variables: { input } };
function Client() {
  return <NewPipelineScreen {...useNewPipeline()} />;
}
function setup(responses: MockedResponse[], granted = ["app.update"]) {
  return render(
    <MockedProvider mocks={responses}>
      <PermissionsProvider value={{ granted: new Set(granted), loading: false }}>
        <Client />
      </PermissionsProvider>
    </MockedProvider>
  );
}
async function fill() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Name"), " Build ");
  await user.type(screen.getByLabelText("Repository URL"), input.repoUrl);
  await user.type(screen.getByLabelText("TOML path"), input.tomlPath);
  return user;
}
beforeEach(() => vi.clearAllMocks());
describe("pipeline creation through Apollo", () => {
  it("matches the committed backend GraphQL input and payload", () => {
    expect(validate(buildSchema(readFileSync("schema.graphql", "utf8")), CREATE_PIPELINE)).toEqual(
      []
    );
  });
  it("sends only the existing organization-owned input and navigates after commit", async () => {
    const result = vi.fn(() => ({
      data: { createPipeline: { ok: true, errors: [], data: { id: "pipeline-guid" } } },
    }));
    setup([{ request, result }]);
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Create pipeline" }));
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/pipelines/pipeline-guid"));
    expect(result).toHaveBeenCalledOnce();
    expect(mocks.success).toHaveBeenCalledWith("Pipeline created");
  });
  it("retains drafts on refusal and retries the same input", async () => {
    setup([
      {
        request,
        result: {
          data: {
            createPipeline: {
              ok: false,
              errors: [{ message: "Organization scope required" }],
              data: null,
            },
          },
        },
      },
      {
        request,
        result: {
          data: { createPipeline: { ok: true, errors: [], data: { id: "pipeline-guid" } } },
        },
      },
    ]);
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Create pipeline" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Organization scope required");
    expect(screen.getByLabelText("Name")).toHaveValue(" Build ");
    expect(mocks.push).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Create pipeline" }));
    await waitFor(() => expect(mocks.push).toHaveBeenCalledOnce());
  });
  it("retains input after a transport failure", async () => {
    setup([{ request, error: new Error("Connection interrupted") }]);
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Create pipeline" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Connection interrupted");
    expect(screen.getByLabelText("Repository URL")).toHaveValue(input.repoUrl);
    expect(mocks.success).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Create pipeline" })).toBeEnabled();
  });
  it("keeps the committed ID when navigation fails and offers a real link", async () => {
    mocks.push.mockImplementationOnce(() => {
      throw new Error("Navigation interrupted");
    });
    const result = vi.fn(() => ({
      data: { createPipeline: { ok: true, errors: [], data: { id: "pipeline-guid" } } },
    }));
    setup([{ request, result }]);
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Create pipeline" }));
    expect(await screen.findByRole("link", { name: "Open pipeline" })).toHaveAttribute(
      "href",
      "/pipelines/pipeline-guid"
    );
    expect(screen.queryByRole("button", { name: "Create pipeline" })).not.toBeInTheDocument();
    expect(result).toHaveBeenCalledOnce();
    expect(mocks.warning).toHaveBeenCalledOnce();
  });
  it("read permission alone cannot invoke pipeline creation", () => {
    setup([], ["app.read"]);
    expect(screen.queryByRole("button", { name: "Create pipeline" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("update apps in this organization");
  });
});
