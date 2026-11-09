import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { NvkeOperatorInstallPlanTreePanel } from "./NvkeOperatorInstallPlanTreePanel";
import { INSTALL_TREE_PROPS as p } from "./NvkeOperatorInstallPlanTreePanel.stories";
const wrapper = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);
const t = messages.nvke.installTree;

it("shows only the saved chart result, exact byte strings and remaining verification", () => {
  render(<NvkeOperatorInstallPlanTreePanel {...p} />, { wrapper });
  expect(screen.getByText(t.verificationRequired)).toBeVisible();
  expect(screen.getByText("9007199254740994")).toBeVisible();
  expect(screen.getByText(p.artifacts!.treeDigest)).toBeVisible();
  expect(screen.getByRole("button", { name: t.prepare })).toBeDisabled();
});
it.each([
  "permission",
  "plan",
  "parent-version",
  "source-version",
  "instance",
  "unverified",
  "loading",
  "error",
  "wrong-artifact",
  "charts",
  "chart-version",
  "chart-digest",
])("hides a saved result after %s invalidation", (kind) => {
  const props = { ...p };
  if (kind === "permission") props.canReview = false;
  if (kind === "plan") props.plan = null;
  if (kind === "charts") props.charts = null;
  if (kind === "chart-version") props.charts = { ...p.charts!, version: p.charts!.version + 1 };
  if (kind === "chart-digest") props.charts = { ...p.charts!, artifactDigest: "a".repeat(64) };
  if (kind === "parent-version") props.plan = { ...p.plan!, version: p.plan!.version + 1 };
  if (kind === "source-version")
    props.operation = { ...p.operation, version: p.operation.version + 1 };
  if (kind === "instance") props.authority = { ...p.authority!, instanceId: p.artifacts!.id };
  if (kind === "unverified") props.authority = { ...p.authority!, verificationCode: "UNVERIFIED" };
  if (kind === "loading") props.loading = true;
  if (kind === "error") props.error = "PRIVATE_PEER_ERROR";
  if (kind === "wrong-artifact") props.retainedArtifactId = p.plan!.id;
  render(<NvkeOperatorInstallPlanTreePanel {...props} />, { wrapper });
  expect(screen.queryByText(p.artifacts!.treeDigest)).toBeNull();
  expect(screen.queryByText("PRIVATE_PEER_ERROR")).toBeNull();
  if (["permission", "plan", "source-version", "instance", "unverified"].includes(kind))
    expect(screen.getByLabelText(t.artifactId)).toHaveValue("");
});
it("prepares every reviewed chart option and retains only its UUID after a lost reply", async () => {
  const onPrepare = vi.fn(async () => false),
    onReview = vi.fn(async () => true),
    onRetryPreparation = vi.fn(async () => true);
  render(
    <NvkeOperatorInstallPlanTreePanel
      {...p}
      artifacts={null}
      onPrepare={onPrepare}
      onReview={onReview}
      onRetryPreparation={onRetryPreparation}
    />,
    { wrapper }
  );
  fireEvent.change(screen.getByLabelText(t.artifactId), { target: { value: p.artifacts!.id } });
  fireEvent.change(screen.getByLabelText(t.treeLimit), {
    target: { value: "9007199254740994" },
  });
  fireEvent.click(screen.getByRole("button", { name: t.prepare }));
  await waitFor(() =>
    expect(onPrepare).toHaveBeenCalledExactlyOnceWith({
      artifactId: p.artifacts!.id,
      maximumBytes: "9007199254740994",
    })
  );
  await waitFor(() => expect(screen.getByText(t.unconfirmed)).toBeVisible());
  expect(screen.queryByLabelText(t.treeLimit)).toBeNull();
  expect(screen.getByLabelText(t.artifactId)).toHaveValue(p.artifacts!.id);
  expect(screen.getByRole("button", { name: t.prepare })).toBeDisabled();
  expect(onReview).not.toHaveBeenCalled();
  expect(onRetryPreparation).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: t.review }));
  await waitFor(() => expect(onReview).toHaveBeenCalledExactlyOnceWith(p.artifacts!.id));
  fireEvent.click(screen.getByRole("button", { name: t.retry }));
  await waitFor(() => expect(onRetryPreparation).toHaveBeenCalledExactlyOnceWith(p.artifacts!.id));
  expect(onPrepare).toHaveBeenCalledOnce();
});
it("keeps UUID generation explicit and never prepares automatically", () => {
  const onPrepare = vi.fn(async () => true);
  render(<NvkeOperatorInstallPlanTreePanel {...p} artifacts={null} onPrepare={onPrepare} />, {
    wrapper,
  });
  fireEvent.click(screen.getByRole("button", { name: t.newId }));
  expect(screen.getByLabelText(t.artifactId)).not.toHaveValue("");
  expect(onPrepare).not.toHaveBeenCalled();
});
it("clears form settings when the current actor/source epoch changes", () => {
  const view = render(<NvkeOperatorInstallPlanTreePanel {...p} artifacts={null} />, { wrapper });
  fireEvent.change(screen.getByLabelText(t.treeLimit), {
    target: { value: "9007199254740994" },
  });
  view.rerender(
    <NvkeOperatorInstallPlanTreePanel {...p} artifacts={null} scopeKey="another-actor" />
  );
  expect(screen.getByLabelText(t.treeLimit)).toHaveValue("");
});
it("allows inspection by another reviewer while disabling retained private retry", () => {
  render(<NvkeOperatorInstallPlanTreePanel {...p} canRetry={false} />, { wrapper });
  expect(screen.getByRole("button", { name: t.review })).toBeEnabled();
  expect(screen.getByRole("button", { name: t.retry })).toBeDisabled();
});
it("rejects an authorizing result without displaying it or enabling a fresh preparation", () => {
  render(
    <NvkeOperatorInstallPlanTreePanel
      {...p}
      artifacts={{ ...p.artifacts!, continuationAuthorized: true } as unknown as typeof p.artifacts}
    />,
    { wrapper }
  );
  expect(screen.queryByText(p.artifacts!.treeDigest)).toBeNull();
  expect(screen.getByRole("button", { name: t.prepare })).toBeDisabled();
});
it("never echoes an exception from a chart callback", async () => {
  render(
    <NvkeOperatorInstallPlanTreePanel
      {...p}
      onReview={async () => {
        throw new Error("PRIVATE_PEER_ERROR");
      }}
    />,
    { wrapper }
  );
  fireEvent.click(screen.getByRole("button", { name: t.review }));
  await waitFor(() => expect(screen.getByText(t.unconfirmed)).toBeVisible());
  expect(screen.queryByText("PRIVATE_PEER_ERROR")).toBeNull();
});

it.each(["1", "0", "9223372036854775808"])(
  "refuses an insufficient or invalid tree budget %s before the callback",
  async (maximumBytes) => {
    const onPrepare = vi.fn(async () => true);
    render(<NvkeOperatorInstallPlanTreePanel {...p} artifacts={null} onPrepare={onPrepare} />, {
      wrapper,
    });
    fireEvent.change(screen.getByLabelText(t.artifactId), { target: { value: p.artifacts!.id } });
    fireEvent.change(screen.getByLabelText(t.treeLimit), { target: { value: maximumBytes } });
    fireEvent.click(screen.getByRole("button", { name: t.prepare }));
    await waitFor(() => expect(screen.getByText(t.invalidInput)).toBeVisible());
    expect(onPrepare).not.toHaveBeenCalled();
  }
);
it("rejects the chart UUID as a new tree identity", async () => {
  const onPrepare = vi.fn(async () => true);
  render(<NvkeOperatorInstallPlanTreePanel {...p} artifacts={null} onPrepare={onPrepare} />, {
    wrapper,
  });
  fireEvent.change(screen.getByLabelText(t.artifactId), { target: { value: p.charts!.id } });
  fireEvent.change(screen.getByLabelText(t.treeLimit), { target: { value: p.artifacts!.bytes } });
  fireEvent.click(screen.getByRole("button", { name: t.prepare }));
  await waitFor(() => expect(screen.getByText(t.invalidInput)).toBeVisible());
  expect(onPrepare).not.toHaveBeenCalled();
});
