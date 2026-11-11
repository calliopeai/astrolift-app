import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import {
  NvkeOperatorInstallPlanPullSecretAnalysisPanel,
  type NvkeOperatorInstallPlanPullSecretAnalysisPanelProps,
} from "./NvkeOperatorInstallPlanPullSecretAnalysisPanel";
import { INSTALL_PULL_SECRET_ANALYSIS_PROPS as fixture } from "./NvkeOperatorInstallPlanPullSecretAnalysisPanel.stories";

const text = en.nvke.installImmutable,
  pullText = en.nvke.installPullSecretAnalysis;
const id = "db84f977-f607-4afc-bbc8-58ea3fa78c2f";
const privateScope = JSON.stringify([
  {
    podKey: "private-namespace/private-pod",
    imagePointer: "/spec/containers/0/image",
    ensureSecretPulledImages: true,
  },
]);
function panel(props: NvkeOperatorInstallPlanPullSecretAnalysisPanelProps) {
  return (
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <NvkeOperatorInstallPlanPullSecretAnalysisPanel {...props} />
    </NextIntlClientProvider>
  );
}
it("shows the validated observation and leaves installation admission unavailable", () => {
  render(panel(fixture));
  expect(screen.getByText(fixture.artifacts!.authorityDigest)).toBeTruthy();
  expect(screen.getByText(pullText.closureRequired)).toBeTruthy();
  expect(screen.getByRole("button", { name: text.prepare })).toBeDisabled();
  expect(screen.queryByRole("button", { name: /execute|continue|install now/i })).toBeNull();
});
it("clears private scope after a lost response and requires explicit review", async () => {
  const onPrepare = vi.fn(async () => false),
    onReview = vi.fn(async () => true),
    onRetryPreparation = vi.fn(async () => true);
  render(panel({ ...fixture, artifacts: null, onPrepare, onReview, onRetryPreparation }));
  fireEvent.change(screen.getByLabelText(en.nvke.installTree.artifactId), {
    target: { value: id },
  });
  fireEvent.change(screen.getByLabelText(pullText.imageSelections), {
    target: { value: privateScope },
  });
  fireEvent.click(screen.getByRole("button", { name: text.prepare }));
  await waitFor(() => expect(onPrepare).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.getByText(en.nvke.installTree.unconfirmed)).toBeTruthy());
  expect(screen.getByLabelText(en.nvke.installTree.artifactId)).toHaveValue(id);
  expect(screen.queryByDisplayValue(privateScope)).toBeNull();
  expect(onReview).not.toHaveBeenCalled();
  expect(onRetryPreparation).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: text.review }));
  await waitFor(() => expect(onReview).toHaveBeenCalledWith(id));
  expect(onPrepare).toHaveBeenCalledTimes(1);
});
it.each([
  { binding: null },
  { pullAuthority: null },
  { canReview: false },
  { pending: true },
  { loading: true },
  { authority: { ...fixture.authority!, verificationCode: "UNVERIFIED" } },
  { operation: { ...fixture.operation, version: fixture.operation.version + 1 } },
])("disables controls for an unavailable or busy reviewed source", (change) => {
  render(panel({ ...fixture, ...change }));
  for (const name of [text.prepare, text.review, text.retry])
    expect(screen.getByRole("button", { name })).toBeDisabled();
});
it("hides a saved observation after the binding version changes", () => {
  render(
    panel({ ...fixture, binding: { ...fixture.binding!, version: fixture.binding!.version + 1 } })
  );
  expect(screen.queryByText(fixture.artifacts!.authorityDigest)).toBeNull();
  expect(screen.getByText(pullText.parentRequired)).toBeTruthy();
  for (const name of [text.prepare, text.review, text.retry])
    expect(screen.getByRole("button", { name })).toBeDisabled();
});
it("does not allow another reviewer to retry the retained private intent", () => {
  render(panel({ ...fixture, canRetry: false }));
  expect(screen.getByRole("button", { name: text.retry })).toBeDisabled();
  expect(screen.getByRole("button", { name: text.review })).not.toBeDisabled();
});
it("retains the selected identity and clears private scope after a thrown callback", async () => {
  const onPrepare = vi.fn(async () => {
    throw new Error("PRIVATE");
  });
  render(panel({ ...fixture, artifacts: null, onPrepare }));
  fireEvent.change(screen.getByLabelText(en.nvke.installTree.artifactId), {
    target: { value: id },
  });
  fireEvent.change(screen.getByLabelText(pullText.imageSelections), {
    target: { value: privateScope },
  });
  fireEvent.click(screen.getByRole("button", { name: text.prepare }));
  await waitFor(() => expect(screen.getByText(en.nvke.installTree.unconfirmed)).toBeTruthy());
  expect(screen.queryByText("PRIVATE")).toBeNull();
  expect(screen.queryByDisplayValue(privateScope)).toBeNull();
  expect(screen.getByLabelText(en.nvke.installTree.artifactId)).toHaveValue(id);
});

it("admits only one preparation callback while asynchronous form submissions overlap", async () => {
  let finish: (value: boolean) => void = () => {
    throw new Error("Preparation has not started");
  };
  const onPrepare = vi.fn(
    () =>
      new Promise<boolean>((resolve) => {
        finish = resolve;
      })
  );
  render(panel({ ...fixture, artifacts: null, onPrepare }));
  fireEvent.change(screen.getByLabelText(en.nvke.installTree.artifactId), {
    target: { value: id },
  });
  fireEvent.change(screen.getByLabelText(pullText.imageSelections), {
    target: { value: privateScope },
  });
  const form = screen.getByRole("button", { name: text.prepare }).closest("form")!;
  fireEvent.submit(form);
  fireEvent.submit(form);
  await waitFor(() => expect(onPrepare).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("button", { name: text.prepare })).toBeDisabled();
  await act(async () => finish(false));
  await waitFor(() => expect(screen.getByText(en.nvke.installTree.unconfirmed)).toBeTruthy());
  expect(onPrepare).toHaveBeenCalledTimes(1);
});

it("clears private selections when permission or reviewed source changes, including a return to the old source", () => {
  const props = { ...fixture, artifacts: null };
  const rendered = render(panel(props));
  fireEvent.change(screen.getByLabelText(pullText.imageSelections), {
    target: { value: privateScope },
  });
  rendered.rerender(panel({ ...props, canReview: false }));
  expect(screen.queryByDisplayValue(privateScope)).toBeNull();
  rendered.rerender(panel(props));
  expect(screen.getByLabelText(pullText.imageSelections)).toHaveValue("");
  expect(screen.getByLabelText(en.nvke.installTree.artifactId)).toHaveValue("");
});

it("requires explicit recovery and never displays private exception messages", async () => {
  const onRetryPreparation = vi.fn(async () => {
    throw new Error("PRIVATE_SECRET");
  });
  const onReview = vi.fn(async () => true);
  render(
    panel({ ...fixture, artifacts: null, retainedArtifactId: id, onRetryPreparation, onReview })
  );
  expect(onRetryPreparation).not.toHaveBeenCalled();
  expect(onReview).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: text.retry }));
  await waitFor(() => expect(onRetryPreparation).toHaveBeenCalledWith(id));
  expect(screen.queryByText("PRIVATE_SECRET")).toBeNull();
  expect(onReview).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: text.prepare })).toBeDisabled();
});

it("clears private image selections across pull-observation changes and return to the old source", () => {
  const props = { ...fixture, artifacts: null };
  const rendered = render(panel(props));
  fireEvent.change(screen.getByLabelText(pullText.imageSelections), {
    target: { value: privateScope },
  });
  rendered.rerender(
    panel({
      ...props,
      pullAuthority: { ...props.pullAuthority!, version: props.pullAuthority!.version + 1 },
    })
  );
  expect(screen.queryByDisplayValue(privateScope)).toBeNull();
  rendered.rerender(panel(props));
  expect(screen.getByLabelText(pullText.imageSelections)).toHaveValue("");
});
it("hides a saved interpretation when the reviewed pull observation version changes", () => {
  render(
    panel({
      ...fixture,
      pullAuthority: { ...fixture.pullAuthority!, version: fixture.pullAuthority!.version + 1 },
    })
  );
  expect(screen.queryByText(fixture.artifacts!.authorityDigest)).toBeNull();
  expect(screen.getByText(text.unavailable)).toBeTruthy();
});
