import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { NvkeOperatorAgentPanel, type NvkeOperatorAgentPanelProps } from "./NvkeOperatorAgentPanel";
import { OPERATOR_AGENT_AUTHORITY } from "./NvkeOperatorAgentPanel.stories";
import { operatorAgentAuthoritySchema } from "./schema";
import { SITE } from "./fixtures";

const wrapper = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);
const props = (): NvkeOperatorAgentPanelProps => ({
  site: SITE,
  authority: OPERATOR_AGENT_AUTHORITY,
  loading: false,
  pending: false,
  error: null,
  canManage: true,
  onRefresh: vi.fn(),
  onConfigure: vi.fn(async () => true),
  onAction: vi.fn(async () => true),
});

it("keeps replacement mTLS credentials out of review and clears the private draft", async () => {
  const p = props();
  render(<NvkeOperatorAgentPanel {...p} />, { wrapper });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.configure }));
  fireEvent.click(screen.getByRole("combobox"));
  fireEvent.click(await screen.findByRole("option", { name: messages.nvke.replace }));
  fireEvent.change(screen.getByLabelText(messages.nvke.clientCertificatePem), {
    target: { value: "PRIVATE_CERT" },
  });
  fireEvent.change(screen.getByLabelText(messages.nvke.clientKeyPem), {
    target: { value: "PRIVATE_KEY" },
  });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  const review = await screen.findByRole("alertdialog");
  expect(review).not.toHaveTextContent("PRIVATE");
  expect(screen.queryByDisplayValue("PRIVATE_KEY")).not.toBeInTheDocument();
  expect(screen.queryByDisplayValue("PRIVATE_CERT")).not.toBeInTheDocument();
  expect(review).toHaveTextContent(OPERATOR_AGENT_AUTHORITY.endpoint);
  expect(review).toHaveTextContent(OPERATOR_AGENT_AUTHORITY.instanceId);
  expect(review).toHaveTextContent(OPERATOR_AGENT_AUTHORITY.bundleManifestSha256);
  fireEvent.click(
    within(review).getByRole("button", { name: messages.shared.confirmation.confirm })
  );
  await waitFor(() => expect(p.onConfigure).toHaveBeenCalledOnce());
  expect(p.onConfigure).toHaveBeenCalledWith(
    expect.objectContaining({
      credentialMode: "replace",
      clientCertificatePem: "PRIVATE_CERT",
      clientKeyPem: "PRIVATE_KEY",
    })
  );
  await waitFor(() => expect(screen.queryByDisplayValue("PRIVATE_KEY")).not.toBeInTheDocument());
});
it("reviews exact site and agent versions before removal", () => {
  const p = props();
  render(<NvkeOperatorAgentPanel {...p} />, { wrapper });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.agentRemove }));
  const review = screen.getByRole("alertdialog");
  expect(review).toHaveTextContent(`site version ${SITE.version}`);
  expect(review).toHaveTextContent(`agent authority version ${OPERATOR_AGENT_AUTHORITY.version}`);
  expect(p.onAction).not.toHaveBeenCalled();
});
it("blocks writes for failed, pending, loading and read-only authority", () => {
  for (const changed of [
    { canManage: false },
    { error: "Refresh authority" },
    { pending: true },
    { loading: true },
  ]) {
    const p = { ...props(), ...changed },
      view = render(<NvkeOperatorAgentPanel {...p} />, { wrapper });
    const button = screen.queryByRole("button", { name: messages.nvke.configure });
    if (button) expect(button).toBeDisabled();
    view.unmount();
  }
});
it.each(["site", "authority", "permission"])(
  "discards an open private draft on %s change",
  async (changed) => {
    const p = props(),
      view = render(<NvkeOperatorAgentPanel {...p} />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: messages.nvke.configure }));
    fireEvent.click(screen.getByRole("combobox"));
    fireEvent.click(await screen.findByRole("option", { name: messages.nvke.replace }));
    fireEvent.change(screen.getByLabelText(messages.nvke.clientKeyPem), {
      target: { value: "PRIVATE_DRAFT" },
    });
    const next =
      changed === "site"
        ? { site: { ...SITE, version: SITE.version + 1 } }
        : changed === "authority"
          ? {
              authority: {
                ...OPERATOR_AGENT_AUTHORITY,
                version: OPERATOR_AGENT_AUTHORITY.version + 1,
              },
            }
          : { canManage: false };
    view.rerender(<NvkeOperatorAgentPanel {...p} {...next} />);
    expect(screen.queryByDisplayValue("PRIVATE_DRAFT")).not.toBeInTheDocument();
    expect(p.onConfigure).not.toHaveBeenCalled();
  }
);
it("clears private credentials and shows a safe failure if a callback throws", async () => {
  const p = {
    ...props(),
    onConfigure: vi.fn(async () => {
      throw new Error("PRIVATE_FAILURE");
    }),
  };
  const view = render(<NvkeOperatorAgentPanel {...p} />, { wrapper });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.configure }));
  fireEvent.click(screen.getByRole("combobox"));
  fireEvent.click(await screen.findByRole("option", { name: messages.nvke.replace }));
  fireEvent.change(screen.getByLabelText(messages.nvke.clientCertificatePem), {
    target: { value: "PRIVATE_CERT" },
  });
  fireEvent.change(screen.getByLabelText(messages.nvke.clientKeyPem), {
    target: { value: "PRIVATE_KEY" },
  });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  const review = await screen.findByRole("alertdialog");
  fireEvent.click(
    within(review).getByRole("button", { name: messages.shared.confirmation.confirm })
  );
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(messages.nvke.configurationFailed)
  );
  expect(screen.queryByDisplayValue("PRIVATE_KEY")).not.toBeInTheDocument();
  expect(screen.queryByText("PRIVATE_FAILURE")).not.toBeInTheDocument();
  view.rerender(<NvkeOperatorAgentPanel {...p} error={messages.nvke.configurationFailed} />);
  expect(screen.getAllByRole("alert")).toHaveLength(1);
  expect(screen.getByRole("button", { name: messages.nvke.configure })).toBeDisabled();
});
it("requires paired mTLS for replacement and refuses unsafe agent authority", () => {
  const valid = {
    endpoint: OPERATOR_AGENT_AUTHORITY.endpoint,
    instanceId: OPERATOR_AGENT_AUTHORITY.instanceId,
    bundleManifestSha256: OPERATOR_AGENT_AUTHORITY.bundleManifestSha256,
    timeoutSeconds: 30,
    credentialMode: "preserve",
    caPem: "",
    clientCertificatePem: "",
    clientKeyPem: "",
  };
  expect(operatorAgentAuthoritySchema.safeParse(valid).success).toBe(true);
  for (const changed of [
    { endpoint: "http://agent.test" },
    { endpoint: "https://user:private@agent.test" },
    { endpoint: "https://agent.test/path" },
    { endpoint: "https://agent.test?private=1" },
    { endpoint: "https://agent.test/.." },
    { instanceId: "invalid" },
    { bundleManifestSha256: "A".repeat(64) },
    { timeoutSeconds: 0 },
    { credentialMode: "clear" },
    { credentialMode: "replace", clientKeyPem: "PRIVATE" },
  ])
    expect(operatorAgentAuthoritySchema.safeParse({ ...valid, ...changed }).success).toBe(false);
});

it("submits the reviewed target even if the underlying form changes after review", async () => {
  const p = props();
  render(<NvkeOperatorAgentPanel {...p} />, { wrapper });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.configure }));
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  const review = await screen.findByRole("alertdialog");
  fireEvent.change(screen.getByLabelText(messages.nvke.endpoint), {
    target: { value: "https://changed-after-review.example.test" },
  });
  fireEvent.click(
    within(review).getByRole("button", { name: messages.shared.confirmation.confirm })
  );
  await waitFor(() => expect(p.onConfigure).toHaveBeenCalledOnce());
  expect(p.onConfigure).toHaveBeenCalledWith(
    expect.objectContaining({ endpoint: OPERATOR_AGENT_AUTHORITY.endpoint })
  );
});
