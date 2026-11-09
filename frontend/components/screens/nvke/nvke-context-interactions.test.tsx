import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { NvkeOperatorInstallPlanContextPanel } from "./NvkeOperatorInstallPlanContextPanel";
import { INSTALL_CONTEXT_PROPS as fixture } from "./NvkeOperatorInstallPlanContextPanel.stories";
const copy = messages.nvke.installContext,
  common = messages.nvke.installTree;
const row = fixture.artifacts!,
  selection = { item: row.item, ordinal: row.ordinal };
function Context({ children }: { children: React.ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      {children}
    </NextIntlClientProvider>
  );
}
function choose() {
  fireEvent.change(screen.getByLabelText(common.artifactId), { target: { value: row.id } });
  fireEvent.change(screen.getByLabelText(copy.item), { target: { value: selection.item } });
  fireEvent.change(screen.getByLabelText(copy.ordinal), {
    target: { value: String(selection.ordinal) },
  });
}
it("captures explicit native render settings and preserves a large revision as text", async () => {
  const onPrepare = vi.fn().mockResolvedValue(false),
    onReview = vi.fn().mockResolvedValue(true);
  render(
    <NvkeOperatorInstallPlanContextPanel
      {...fixture}
      artifacts={null}
      retainedSelection={null}
      onPrepare={onPrepare}
      onReview={onReview}
    />,
    { wrapper: Context }
  );
  choose();
  fireEvent.change(screen.getByLabelText(copy.revision), { target: { value: "9007199254740993" } });
  fireEvent.click(screen.getByRole("checkbox", { name: copy.upgrade }));
  fireEvent.click(screen.getByRole("button", { name: copy.prepare }));
  await waitFor(() => expect(onPrepare).toHaveBeenCalledTimes(1));
  expect(onPrepare).toHaveBeenCalledWith({
    artifactId: row.id,
    item: selection.item,
    ordinal: String(selection.ordinal),
    revision: "9007199254740993",
    upgrade: true,
    maximumBytes: "16777216",
    maximumArtifactBytes: "1048576",
    maximumCalls: "512",
    maximumIterations: "64",
    timeoutSeconds: "300",
  });
  await waitFor(() => expect(screen.getByText(common.unconfirmed)).toBeInTheDocument());
  expect(screen.getByLabelText(common.artifactId)).toHaveValue(row.id);
  expect(screen.getByLabelText(common.artifactId)).toBeDisabled();
  expect(screen.queryByRole("button", { name: common.newId })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: copy.review }));
  await waitFor(() => expect(onReview).toHaveBeenCalledWith(row.id, selection));
  expect(onPrepare).toHaveBeenCalledTimes(1);
});
it("allows source-bound review without requiring private capture settings", async () => {
  const onReview = vi.fn().mockResolvedValue(true),
    onPrepare = vi.fn();
  render(
    <NvkeOperatorInstallPlanContextPanel
      {...fixture}
      artifacts={null}
      retainedSelection={null}
      canRetry={false}
      onReview={onReview}
      onPrepare={onPrepare}
    />,
    { wrapper: Context }
  );
  choose();
  fireEvent.change(screen.getByLabelText(copy.maximumBytes), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: copy.review }));
  await waitFor(() => expect(onReview).toHaveBeenCalledWith(row.id, selection));
  expect(onPrepare).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: copy.retry })).toBeDisabled();
});
it.each(["permission", "tree-parent-version", "authority", "operation"])(
  "hides context evidence and refuses calls after %s invalidation",
  (mode) => {
    const onReview = vi.fn(),
      onPrepare = vi.fn();
    const props = { ...fixture, onReview, onPrepare };
    if (mode === "permission") props.canReview = false;
    if (mode === "tree-parent-version")
      props.tree = { ...fixture.tree!, planVersion: fixture.tree!.planVersion + 1 };
    if (mode === "authority") props.authority = { ...fixture.authority!, isEnabled: false };
    if (mode === "operation")
      props.operation = { ...fixture.operation, version: fixture.operation.version + 1 };
    render(<NvkeOperatorInstallPlanContextPanel {...props} />, { wrapper: Context });
    expect(screen.queryByText(row.contextDigest)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: copy.review })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: copy.review }));
    expect(onReview).not.toHaveBeenCalled();
    expect(onPrepare).not.toHaveBeenCalled();
  }
);
it("never turns incomplete item selection into ordinal zero", async () => {
  const onReview = vi.fn();
  render(
    <NvkeOperatorInstallPlanContextPanel
      {...fixture}
      artifacts={null}
      retainedSelection={null}
      onReview={onReview}
    />,
    { wrapper: Context }
  );
  fireEvent.change(screen.getByLabelText(common.artifactId), { target: { value: row.id } });
  fireEvent.change(screen.getByLabelText(copy.item), { target: { value: selection.item } });
  fireEvent.click(screen.getByRole("button", { name: copy.review }));
  await waitFor(() => expect(screen.getByText(copy.invalidInput)).toBeInTheDocument());
  expect(onReview).not.toHaveBeenCalled();
});
