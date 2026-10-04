import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { hydrateRoot } from "react-dom/client";
import { NextIntlClientProvider, createTranslator } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";
import { ModelConnectionIntakePanel } from "./ModelConnectionIntakePanel";
import { connectionIntakeFixture } from "./ModelConnectionIntakePanel.stories";
import { ModelConnectionRequestScreen } from "./ModelConnectionRequestScreen";
import { ModelConnectionRequestsScreen } from "./ModelConnectionRequestsScreen";
import { ModelConnectionPolicyPanel } from "./ModelConnectionPolicyPanel";
import {
  connectionPolicyProps,
  connectionRequestProps,
  connectionRequestRow,
} from "./model-connection.fixtures";
import { fakeModelPage } from "./shared-model.fixtures";
const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
function wrapper(locale: keyof typeof catalogs = "en") {
  return function Provider({ children }: { children: React.ReactNode }) {
    return (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        timeZone="America/Costa_Rica"
      >
        {children}
      </NextIntlClientProvider>
    );
  };
}
describe("literal request facts and context-bound review", () => {
  it.each(Object.keys(catalogs) as (keyof typeof catalogs)[])(
    "%s hydrates request and policy review without identity or timezone rewrite",
    async (locale) => {
      const t = createTranslator({
        locale,
        messages: catalogs[locale],
        namespace: "models.shared.connections",
      });
      const page = fakeModelPage({
        rows: [
          {
            ...connectionRequestRow,
            modelName: "<engine & one>",
            requesterUsername: "UserCase@example.test",
          },
        ],
      });
      page.list.definition.searchable = false;
      const view = (
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          timeZone="America/Costa_Rica"
        >
          <ModelConnectionRequestsScreen
            page={page}
            review={false}
            onReview={() => {}}
            supported
            supportError={null}
            onRetrySupport={() => {}}
          />
          <ModelConnectionPolicyPanel {...connectionPolicyProps} />
        </NextIntlClientProvider>
      );
      const markup = renderToString(view);
      expect(markup).toContain("&lt;engine &amp; one&gt;");
      expect(markup).toContain("UserCase@example.test");
      expect(markup).toContain(t("pending"));
      const element = document.createElement("div");
      element.innerHTML = markup;
      document.body.append(element);
      const recover = vi.fn();
      const root = hydrateRoot(element, view, { onRecoverableError: recover });
      try {
        await act(async () => {});
        expect(recover).not.toHaveBeenCalled();
        const text = new Intl.DateTimeFormat(locale, {
          dateStyle: "medium",
          timeStyle: "short",
          timeZone: "America/Costa_Rica",
        }).format(new Date(connectionRequestRow.createdAt!));
        expect(element.textContent).toContain(text);
      } finally {
        await act(async () => root.unmount());
        element.remove();
      }
    }
  );
  it("unknown future status stays literal without inferred actions", () => {
    render(
      <ModelConnectionRequestScreen
        {...connectionRequestProps}
        row={{
          ...connectionRequestRow,
          status: "FUTURE_UNKNOWN" as typeof connectionRequestRow.status,
          canCancel: false,
        }}
      />,
      { wrapper: wrapper() }
    );
    expect(screen.getByText("FUTURE_UNKNOWN")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
  });
  it("withdrawn overlay read cannot reuse old review after A→B→A", async () => {
    const write = vi.fn(connectionPolicyProps.onSubmit);
    const view = render(
      <ModelConnectionPolicyPanel {...connectionPolicyProps} onSubmit={write} />,
      { wrapper: wrapper() }
    );
    fireEvent.click(screen.getByRole("button", { name: en.models.shared.connections.savePolicy }));
    view.rerender(<ModelConnectionPolicyPanel {...connectionPolicyProps} onSubmit={write} stale />);
    view.rerender(<ModelConnectionPolicyPanel {...connectionPolicyProps} onSubmit={write} />);
    expect(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: en.models.shared.connections.savePolicy,
      })
    ).toBeDisabled();
    expect(write).not.toHaveBeenCalled();
  });
  it("late accepted overlay write cannot close or overwrite a newer actor review", async () => {
    let resolve!: (value: Awaited<ReturnType<typeof connectionPolicyProps.onSubmit>>) => void;
    const write = vi.fn(
      () =>
        new Promise<Awaited<ReturnType<typeof connectionPolicyProps.onSubmit>>>((done) => {
          resolve = done;
        })
    );
    const view = render(
      <ModelConnectionPolicyPanel {...connectionPolicyProps} onSubmit={write} />,
      { wrapper: wrapper() }
    );
    fireEvent.click(screen.getByRole("button", { name: en.models.shared.connections.savePolicy }));
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: en.models.shared.connections.savePolicy,
      })
    );
    await waitFor(() => expect(write).toHaveBeenCalledOnce());
    view.rerender(
      <ModelConnectionPolicyPanel {...connectionPolicyProps} scopeKey="anotherActor:org:policy" />
    );
    fireEvent.click(screen.getByRole("button", { name: en.models.shared.connections.savePolicy }));
    await act(async () => resolve({ accepted: true, policy: connectionPolicyProps.policy }));
    expect(screen.getByRole("alertdialog")).toBeVisible();
    expect(screen.queryByText(en.models.shared.connections.policySaved)).toBeNull();
  });
  it("source withdrawal prevents stale intake and old closure after ABA", () => {
    const write = vi.fn(connectionIntakeFixture.onSubmit);
    const view = render(
      <ModelConnectionIntakePanel {...connectionIntakeFixture} onSubmit={write} />,
      { wrapper: wrapper() }
    );
    fireEvent.click(screen.getByRole("button", { name: "storefront / staging" }));
    fireEvent.change(screen.getByLabelText(en.models.shared.connections.alias), {
      target: { value: "chat" },
    });
    fireEvent.click(screen.getByRole("button", { name: en.models.shared.connections.review }));
    view.rerender(
      <ModelConnectionIntakePanel
        {...connectionIntakeFixture}
        targets={{ ...connectionIntakeFixture.targets, rows: [], stale: true }}
        onSubmit={write}
      />
    );
    view.rerender(<ModelConnectionIntakePanel {...connectionIntakeFixture} onSubmit={write} />);
    expect(write).not.toHaveBeenCalled();
  });
});
