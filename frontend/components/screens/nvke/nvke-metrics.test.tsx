import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import metricsStory from "./NvkeMetricsPanel.stories";
import { NvkeMetricsPanel } from "./NvkeMetricsPanel";
import { NvkeCredentialPanel } from "./NvkeCredentialPanel";
import { CREDENTIAL_PROPS } from "./NvkeCredentialPanel.stories";
import { metricsAuthoritySchema } from "./schema";
const wrapper = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);

it("reviews the exact metrics target without credentials and clears private inputs", async () => {
  const submit = vi.fn(async () => true);
  render(<NvkeMetricsPanel {...metricsStory.args} onConfigure={submit} />, { wrapper });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.configure }));
  fireEvent.click(screen.getByRole("combobox"));
  fireEvent.click(await screen.findByRole("option", { name: messages.nvke.replace }));
  fireEvent.change(screen.getByLabelText(messages.nvke.metricsToken), {
    target: { value: "PRIVATE_METRICS_TOKEN" },
  });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  const review = await screen.findByRole("alertdialog");
  expect(review).toHaveTextContent(metricsStory.args.authority.endpoint);
  expect(review).toHaveTextContent("metrics authority version 2");
  expect(review).not.toHaveTextContent("PRIVATE_METRICS_TOKEN");
  fireEvent.click(
    within(review).getByRole("button", { name: messages.shared.confirmation.confirm })
  );
  await waitFor(() => expect(submit).toHaveBeenCalledOnce());
  expect(submit).toHaveBeenCalledWith(
    expect.objectContaining({ token: "PRIVATE_METRICS_TOKEN", allowHttp: false })
  );
  await waitFor(() =>
    expect(screen.queryByDisplayValue("PRIVATE_METRICS_TOKEN")).not.toBeInTheDocument()
  );
});

it("shows retained expiry independently of inspection failure and collection failure", () => {
  render(<NvkeCredentialPanel {...CREDENTIAL_PROPS} />, { wrapper });
  const t = messages.nvke.credentialMetrics;
  expect(screen.getByText(t.collection).nextElementSibling).toHaveTextContent(t.no);
  expect(screen.getByText(t.automation).nextElementSibling).toHaveTextContent(t.unknown);
  expect(screen.getByText("9007199254740993")).toBeInTheDocument();
  expect(screen.getByText("2026-11-01T00:00:00Z")).toBeInTheDocument();
  expect(screen.getByRole("note")).toHaveTextContent(CREDENTIAL_PROPS.observation!.scrapedAt);
  expect(screen.getByText(t.help)).toHaveTextContent("not current health");
});

it("hides private saved rows on read failure and hides authority controls from readers", () => {
  const view = render(<NvkeCredentialPanel {...CREDENTIAL_PROPS} error="Read failed" />, {
    wrapper,
  });
  expect(screen.queryByText("9007199254740993")).not.toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("Read failed");
  view.unmount();
  render(<NvkeMetricsPanel {...metricsStory.args} canManage={false} />, { wrapper });
  expect(screen.queryByRole("button", { name: messages.nvke.configure })).not.toBeInTheDocument();
});

it.each([
  "http://metrics.test/metrics",
  "https://PRIVATE:TOKEN@metrics.test/metrics",
  "https://metrics.test/metrics?token=PRIVATE",
  "https://metrics.test/a/../metrics",
])("refuses unreviewed metrics URL %s", (endpoint) => {
  expect(
    metricsAuthoritySchema.safeParse({
      endpoint,
      allowHttp: false,
      timeoutSeconds: 30,
      credentialMode: "preserve",
      token: "",
      caPem: "",
      clientCertificatePem: "",
      clientKeyPem: "",
    }).success
  ).toBe(false);
});
