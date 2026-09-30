import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import pt from "@/messages/pt-BR.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import {
  ModelSubscriptionsPanel,
  type ModelSubscriptionsPanelProps,
  type SubscriptionActionResult,
  type SubscriptionRequest,
} from "./ModelSubscriptionsPanel";
import { subscriptionProps, fakeModelPage } from "./shared-model.fixtures";

const locales = { en, es, fr, de, "pt-BR": pt, ja, ko, "zh-Hans": zh };
function view(props: ModelSubscriptionsPanelProps, locale: keyof typeof locales = "en") {
  return (
    <NextIntlClientProvider locale={locale} messages={locales[locale]} timeZone="UTC">
      <ModelSubscriptionsPanel {...props} />
    </NextIntlClientProvider>
  );
}
async function review(alias = "assistant", target = "storefront / staging") {
  fireEvent.click(screen.getByRole("button", { name: target }));
  fireEvent.change(screen.getByLabelText("Subscription alias"), { target: { value: alias } });
  fireEvent.click(screen.getByRole("button", { name: "Review subscription" }));
  return screen.findByRole("alertdialog");
}
function deferred() {
  let resolve!: (result: SubscriptionActionResult) => void;
  const promise = new Promise<SubscriptionActionResult>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("shared model subscription review", () => {
  it("dispatches only the reviewed model/environment IDs and versions, then waits for readiness", async () => {
    const onSubscribe = vi.fn(async (): Promise<SubscriptionActionResult> => ({ accepted: true }));
    render(view({ ...subscriptionProps, onSubscribe }));
    const dialog = await review();
    expect(dialog).toHaveTextContent("All consumers may temporarily lose access");
    expect(onSubscribe).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Request subscription" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(onSubscribe).toHaveBeenCalledExactlyOnceWith({
      organizationId: "org",
      modelId: "shared-model",
      modelVersion: 5,
      expectedClusterId: "cluster-one",
      expectedProviderId: "provider-one",
      environmentId: "env-staging",
      environmentVersion: 4,
      alias: "assistant",
    });
    expect(screen.getByText(/Request accepted. Waiting for restart/)).toBeInTheDocument();
    expect(screen.getByText("MODEL_CHAT_")).toBeInTheDocument();
    expect(screen.queryByText("MODEL_ASSISTANT_")).not.toBeInTheDocument();
  });

  it.each(["", "UPPER", "2chat", "has-dash", "a".repeat(33)])(
    "refuses invalid named alias %j before any action",
    async (alias) => {
      const onSubscribe = vi.fn();
      render(view({ ...subscriptionProps, onSubscribe }));
      fireEvent.click(screen.getByRole("button", { name: "storefront / staging" }));
      fireEvent.change(screen.getByLabelText("Subscription alias"), { target: { value: alias } });
      fireEvent.click(screen.getByRole("button", { name: "Review subscription" }));
      expect(
        await screen.findByText("Use a named lowercase alias of at most 32 characters.")
      ).toBeInTheDocument();
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
      expect(onSubscribe).not.toHaveBeenCalled();
    }
  );

  it("keeps a refused confirmation and draft for explicit retry without a success state", async () => {
    const onSubscribe = vi.fn(
      async (): Promise<SubscriptionActionResult> => ({
        accepted: false,
        message: "Destination version changed",
      })
    );
    render(view({ ...subscriptionProps, onSubscribe }));
    await review();
    fireEvent.click(screen.getByRole("button", { name: "Request subscription" }));
    expect(await screen.findByText("Destination version changed")).toBeInTheDocument();
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.queryByText(/Request accepted/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Subscription alias")).toHaveValue("assistant");
    expect(onSubscribe).toHaveBeenCalledTimes(1);
  });

  it("never revives a review after target or model version changes back", async () => {
    const onSubscribe = vi.fn();
    const props = { ...subscriptionProps, onSubscribe };
    const { rerender } = render(view(props));
    await review();
    rerender(
      view({
        ...props,
        targets: {
          ...props.targets,
          rows: props.targets.rows.map((row) => ({ ...row, version: row.version + 1 })),
        },
      })
    );
    expect(screen.getByRole("button", { name: "Request subscription" })).toBeDisabled();
    rerender(view(props));
    expect(screen.getByRole("button", { name: "Request subscription" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Request subscription" }));
    expect(onSubscribe).not.toHaveBeenCalled();
  });

  it("clears old context and ignores a late response across organization A→B→A", async () => {
    const pending = deferred();
    const onSubscribe = vi.fn(() => pending.promise);
    const props = { ...subscriptionProps, onSubscribe };
    const { rerender } = render(view(props));
    await review();
    fireEvent.click(screen.getByRole("button", { name: "Request subscription" }));
    await waitFor(() => expect(onSubscribe).toHaveBeenCalledTimes(1));
    rerender(
      view({ ...props, deployment: { ...props.deployment, organizationId: "foreign-org" } })
    );
    rerender(view(props));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Subscription alias")).toHaveValue("");
    await act(async () => pending.resolve({ accepted: true }));
    expect(screen.queryByText(/Request accepted/)).not.toBeInTheDocument();
    expect(onSubscribe).toHaveBeenCalledTimes(1);
  });

  it("ignores a late accepted reply when the reviewed target is replaced", async () => {
    const pending = deferred();
    const props = { ...subscriptionProps, onSubscribe: vi.fn(() => pending.promise) };
    const { rerender } = render(view(props));
    await review();
    fireEvent.click(screen.getByRole("button", { name: "Request subscription" }));
    await waitFor(() => expect(props.onSubscribe).toHaveBeenCalledTimes(1));
    rerender(view({ ...props, targets: { ...props.targets, rows: [] } }));
    await act(async () => pending.resolve({ accepted: true }));
    expect(screen.queryByText(/Request accepted/)).not.toBeInTheDocument();
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(props.onSubscribe).toHaveBeenCalledTimes(1);
  });

  it("ignores late model-version replies without making the old review reusable", async () => {
    const pending = deferred();
    const props = { ...subscriptionProps, onSubscribe: vi.fn(() => pending.promise) };
    const { rerender } = render(view(props));
    await review();
    fireEvent.click(screen.getByRole("button", { name: "Request subscription" }));
    await waitFor(() => expect(props.onSubscribe).toHaveBeenCalledTimes(1));
    rerender(view({ ...props, deployment: { ...props.deployment, version: 6 } }));
    await act(async () => pending.resolve({ accepted: true }));
    rerender(view(props));
    expect(screen.queryByText(/Request accepted/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Request subscription" })).toBeDisabled();
    expect(props.onSubscribe).toHaveBeenCalledTimes(1);
  });

  it("reviews exact revocation identity and treats accepted revocation as pending", async () => {
    const onRevoke = vi.fn(async (): Promise<SubscriptionActionResult> => ({ accepted: true }));
    render(view({ ...subscriptionProps, onRevoke }));
    fireEvent.click(screen.getByRole("button", { name: "Review revocation" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("storefront / production");
    fireEvent.click(screen.getByRole("button", { name: "Request revocation" }));
    await waitFor(() =>
      expect(onRevoke).toHaveBeenCalledExactlyOnceWith({
        organizationId: "org",
        modelId: "shared-model",
        modelVersion: 5,
        expectedClusterId: "cluster-one",
        expectedProviderId: "provider-one",
        subscriptionId: "subscription-one",
        subscriptionVersion: 2,
      })
    );
    expect(screen.getByText(/Request accepted. Waiting for restart/)).toBeInTheDocument();
    expect(screen.queryByText("Revoked")).not.toBeInTheDocument();
  });

  it("refuses stale revocation after attachment replacement and ignores late completion", async () => {
    const pending = deferred();
    const props = { ...subscriptionProps, onRevoke: vi.fn(() => pending.promise) };
    const { rerender } = render(view(props));
    fireEvent.click(screen.getByRole("button", { name: "Review revocation" }));
    fireEvent.click(screen.getByRole("button", { name: "Request revocation" }));
    await waitFor(() => expect(props.onRevoke).toHaveBeenCalledTimes(1));
    rerender(
      view({
        ...props,
        subscriptions: {
          ...props.subscriptions,
          rows: [{ ...props.subscriptions.rows[0], version: 3 }],
        },
      })
    );
    await act(async () => pending.resolve({ accepted: true }));
    expect(screen.queryByText(/Request accepted/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Request revocation" })).toBeDisabled();
  });

  it("separately refuses operator-disabled new subscriptions while allowing reviewed destination-authorized cleanup", async () => {
    const onSubscribe = vi.fn(),
      onRevoke = vi.fn(async (): Promise<SubscriptionActionResult> => ({ accepted: true }));
    render(
      view({
        ...subscriptionProps,
        deployment: { ...subscriptionProps.deployment, subscriptionsEnabled: false },
        onSubscribe,
        onRevoke,
      })
    );
    expect(
      screen.getByText(en.models.shared.subscriptions.subscriptionsDisabled)
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Review subscription" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "storefront / staging" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Review revocation" }));
    fireEvent.click(screen.getByRole("button", { name: "Request revocation" }));
    await waitFor(() => expect(onRevoke).toHaveBeenCalledTimes(1));
    expect(onSubscribe).not.toHaveBeenCalled();
  });
  it("refuses an allowed target that belongs to another cluster", () => {
    render(
      view({
        ...subscriptionProps,
        targets: {
          ...subscriptionProps.targets,
          rows: subscriptionProps.targets.rows.map((target) => ({
            ...target,
            clusterId: "other-cluster",
          })),
        },
      })
    );
    expect(screen.getByRole("button", { name: "storefront / staging" })).toBeDisabled();
    expect(screen.queryByText("Eligible")).not.toBeInTheDocument();
  });
  it("does not revive a review after provider replacement and return", async () => {
    const onSubscribe = vi.fn(),
      props = { ...subscriptionProps, onSubscribe };
    const { rerender } = render(view(props));
    await review();
    rerender(view({ ...props, deployment: { ...props.deployment, providerId: "provider-two" } }));
    rerender(view(props));
    expect(screen.getByRole("button", { name: "Request subscription" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Request subscription" }));
    expect(onSubscribe).not.toHaveBeenCalled();
  });
  it.each(["unknown", "unsupported"] as const)(
    "blocks unverified runtime %s",
    (runtimeAdmission) => {
      const onSubscribe = vi.fn();
      render(
        view({
          ...subscriptionProps,
          deployment: { ...subscriptionProps.deployment, runtimeAdmission },
          onSubscribe,
        })
      );
      expect(screen.getByRole("button", { name: "Review subscription" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Review revocation" })).toBeDisabled();
      expect(onSubscribe).not.toHaveBeenCalled();
    }
  );

  it("exposes server paging and search callbacks without filtering the page in the browser", async () => {
    const older = vi.fn(),
      setSearch = vi.fn();
    render(
      view({
        ...subscriptionProps,
        targets: fakeModelPage({
          ...subscriptionProps.targets,
          list: { ...subscriptionProps.targets.list, older, setSearch },
          nextCursor: "opaque-page-two",
        }),
      })
    );
    fireEvent.change(screen.getAllByPlaceholderText("Search app environments")[0], {
      target: { value: "later-app" },
    });
    await waitFor(() => expect(setSearch).toHaveBeenCalledWith("later-app"));
    fireEvent.click(screen.getAllByRole("button", { name: "Older" })[0]);
    expect(older).toHaveBeenCalledWith("opaque-page-two");
    expect(screen.getByRole("button", { name: "storefront / staging" })).toBeInTheDocument();
  });

  it.each(Object.keys(locales) as (keyof typeof locales)[])(
    "renders %s copy while alias and binding identifiers remain exact",
    async (locale) => {
      const messages = locales[locale].models.shared.subscriptions;
      const onSubscribe = vi.fn(
        async (_request: SubscriptionRequest): Promise<SubscriptionActionResult> => ({
          accepted: false,
          message: "VERSION_MISMATCH",
        })
      );
      render(view({ ...subscriptionProps, onSubscribe }, locale));
      expect(screen.getByRole("heading", { name: messages.title })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "storefront / staging" }));
      fireEvent.change(screen.getByLabelText(messages.alias), { target: { value: "chat_2" } });
      fireEvent.click(screen.getByRole("button", { name: messages.reviewSubscribe }));
      const dialog = await screen.findByRole("alertdialog");
      expect(dialog).toHaveTextContent(messages.restartImpact);
      fireEvent.click(screen.getByRole("button", { name: messages.confirmSubscribe }));
      await waitFor(() => expect(onSubscribe).toHaveBeenCalledTimes(1));
      expect(onSubscribe.mock.calls[0][0].alias).toBe("chat_2");
      expect(screen.getByText("MODEL_CHAT_")).toBeInTheDocument();
      expect(await screen.findByText("VERSION_MISMATCH")).toBeInTheDocument();
    }
  );
});
