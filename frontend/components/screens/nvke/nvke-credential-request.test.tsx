import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { NvkeCredentialRequestPanel } from "./NvkeCredentialRequestPanel";
import { COLLECTION_PROPS } from "./NvkeCredentialRequestPanel.stories";
const wrapper = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);
it("reviews the exact source, authority, cluster and observation deadline", async () => {
  const submit = vi.fn(async () => true);
  render(<NvkeCredentialRequestPanel {...COLLECTION_PROPS} onRequest={submit} />, { wrapper });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.credentialCollection.collect }));
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  const review = await screen.findByRole("alertdialog");
  for (const value of [
    COLLECTION_PROPS.source.operationId,
    COLLECTION_PROPS.clusterId!,
    COLLECTION_PROPS.authority!.endpoint,
    "600 seconds",
  ])
    expect(review).toHaveTextContent(value);
  fireEvent.click(
    within(review).getByRole("button", { name: messages.shared.confirmation.confirm })
  );
  await waitFor(() => expect(submit).toHaveBeenCalledWith(600));
});
it("rejects invalid deadlines before confirmation and resets forms across scope visits", async () => {
  const submit = vi.fn(async () => true);
  const view = render(<NvkeCredentialRequestPanel {...COLLECTION_PROPS} onRequest={submit} />, {
    wrapper,
  });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.credentialCollection.collect }));
  fireEvent.change(screen.getByLabelText(messages.nvke.credentialCollection.deadline), {
    target: { value: "1" },
  });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  expect(await screen.findByRole("alert")).toHaveTextContent(messages.nvke.invalidForm);
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  view.rerender(
    <NvkeCredentialRequestPanel {...COLLECTION_PROPS} scopeKey="another-visit" onRequest={submit} />
  );
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(submit).not.toHaveBeenCalled();
});
it("permits receipt-only recovery without administrator or authority metadata", () => {
  const recover = vi.fn(async () => true);
  render(
    <NvkeCredentialRequestPanel
      {...COLLECTION_PROPS}
      canManage={false}
      canRecover
      authority={null}
      error="Metadata unavailable"
      recovery={{
        requestId: "123e4567-e89b-12d3-a456-426614174093",
        operationKey: "metrics.credentials",
        timeoutSeconds: 600,
      }}
      onRecover={recover}
    />,
    { wrapper }
  );
  expect(
    screen.queryByRole("button", { name: messages.nvke.credentialCollection.collect })
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.credentialCollection.recover }));
  expect(recover).toHaveBeenCalledOnce();
});
