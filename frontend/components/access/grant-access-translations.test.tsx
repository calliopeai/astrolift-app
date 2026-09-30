import { parse, TYPE, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import de from "@/messages/de.json";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";

import { CHECKOUT, DANA, DEPLOYER, PREVIEW, ROLES, SAM } from "./fixtures";
import { localizedGrantEffect } from "./grant-copy";
import { GrantAccessFlow, type GrantAccessFlowProps } from "./GrantAccessFlow";

const catalogs = { en, es, fr, de, "pt-BR": pt, ja, ko, "zh-Hans": zh };
const draft = {
  principals: [DANA, SAM],
  roleId: DEPLOYER.id,
  scope: CHECKOUT,
  expiry: { kind: "never" as const },
};
const defaults: GrantAccessFlowProps = {
  crumbs: [],
  search: { query: "", setQuery: () => {}, results: [] },
  roles: ROLES,
  scopeTree: { roots: [] },
  preview: async () => PREVIEW,
  onSubmit: async () => [],
  onDone: () => {},
  onCancel: () => {},
  expirySupported: true,
};

describe.each(Object.entries(catalogs))("%s grant flow", (locale, messages) => {
  const t = createTranslator({ locale, messages, namespace: "shared.access.grant" });
  const accessT = createTranslator({ locale, messages, namespace: "shared.access" });
  const onError = vi.fn();
  const ui = (props: Partial<GrantAccessFlowProps>, timeZone = "UTC") => (
    <NextIntlClientProvider
      locale={locale}
      messages={messages}
      timeZone={timeZone}
      onError={onError}
    >
      <GrantAccessFlow {...defaults} {...props} />
    </NextIntlClientProvider>
  );

  it("validates the actual Who step in the locale without writing or advancing", () => {
    const submit = vi.fn();
    render(ui({ onSubmit: submit }));
    expect(screen.getByRole("heading", { name: t("title") })).toBeVisible();
    expect(screen.getByRole("searchbox", { name: t("searchLabel") })).toHaveAttribute(
      "placeholder",
      t("searchPlaceholder")
    );
    fireEvent.click(screen.getByRole("button", { name: t("continue") }));
    expect(screen.getByRole("alert")).toHaveTextContent(t("pickPrincipal"));
    expect(submit).not.toHaveBeenCalled();
  });

  it("localizes role read failures without advancing past a missing selection", () => {
    render(ui({ initialStep: 1, rolesError: { message: "ROLE_READ_DENIED" } }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      t("rolesFailed", { message: "ROLE_READ_DENIED" })
    );
    fireEvent.click(screen.getByRole("button", { name: t("continue") }));
    expect(screen.getAllByRole("alert").some((node) => node.textContent === t("pickRole"))).toBe(
      true
    );
  });

  it("requires a civil date before review and preserves that exact date in the submitted draft", async () => {
    const submit = vi.fn().mockResolvedValue([
      { principal: DANA, ok: true },
      { principal: SAM, ok: true },
    ]);
    render(ui({ initialDraft: draft, initialStep: 3, onSubmit: submit }));
    fireEvent.click(screen.getByRole("button", { name: t("onDate") }));
    fireEvent.click(screen.getByRole("button", { name: t("continue") }));
    expect(screen.getByRole("alert")).toHaveTextContent(t("pickDate"));
    fireEvent.change(screen.getByLabelText(t("expiryDate")), { target: { value: "2026-10-31" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: t("continue") })));
    fireEvent.click(screen.getByRole("button", { name: t("grantCount", { count: 2 }) }));
    await waitFor(() =>
      expect(submit).toHaveBeenCalledExactlyOnceWith({
        ...draft,
        expiry: { kind: "date", date: "2026-10-31" },
      })
    );
  });

  it("uses exact preview counts and preserves names, permission identifiers and server refusal", () => {
    const p = {
      ...PREVIEW,
      gainingCount: 401,
      alreadyCount: 17,
      refusal: "SERVER_DENIAL: app.deploy {raw}",
      notes: ["SERVER_NOTE <literal>"],
    };
    render(ui({ initialStep: 4, initialDraft: draft, initialPreview: p }));
    expect(screen.getByTestId("grant-effect")).toHaveTextContent(
      localizedGrantEffect(p, DEPLOYER, CHECKOUT, t, accessT)
    );
    expect(screen.getByRole("alert")).toHaveTextContent(p.refusal);
    expect(screen.getByText(p.notes[0])).toBeVisible();
    expect(screen.getByText(DEPLOYER.name)).toBeVisible();
    expect(screen.getByText(CHECKOUT.name)).toBeVisible();
    expect(screen.getByRole("button", { name: t("grantCount", { count: 2 }) })).toBeDisabled();
    for (const count of [0, 1, 7]) {
      const effect = localizedGrantEffect(
        { gaining: [], already: [], gainingCount: count, alreadyCount: 0 },
        null,
        null,
        t,
        accessT
      );
      expect(effect).toBe(t("effect", { count, already: 0, what: t("access"), where: "" }));
    }
    expect(onError).not.toHaveBeenCalled();
  });

  it("keeps successful grants and retries only failed principals, preserving raw failures", async () => {
    const submit = vi
      .fn()
      .mockResolvedValue([{ principal: SAM, ok: false, error: "ROLE_CEILING: app.deploy" }]);
    const done = vi.fn();
    render(
      ui({
        initialStep: 4,
        initialDraft: draft,
        initialPreview: PREVIEW,
        initialOutcomes: [
          { principal: DANA, ok: true },
          { principal: SAM, ok: false, error: "OLD_FAILURE" },
        ],
        onSubmit: submit,
        onDone: done,
      })
    );
    fireEvent.click(screen.getByRole("button", { name: t("retryCount", { count: 1 }) }));
    await screen.findByText("ROLE_CEILING: app.deploy");
    expect(submit).toHaveBeenCalledExactlyOnceWith({ ...draft, principals: [SAM] });
    expect(done).not.toHaveBeenCalled();
    expect(screen.getByText(t("grantedCount", { count: 1 }))).toBeVisible();
    submit.mockResolvedValue([{ principal: SAM, ok: true }]);
    fireEvent.click(screen.getByRole("button", { name: t("retryCount", { count: 1 }) }));
    await waitFor(() =>
      expect(done).toHaveBeenCalledExactlyOnceWith([
        { principal: DANA, ok: true },
        { principal: SAM, ok: true },
      ])
    );
    expect(submit.mock.calls[1][0].principals).toEqual([SAM]);
  });

  it("retains a failed preview on review and localizes only the missing-error fallback", async () => {
    const preview = vi.fn().mockRejectedValue("no error object");
    render(ui({ initialDraft: draft, initialStep: 3, preview }));
    fireEvent.click(screen.getByRole("button", { name: t("continue") }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        t("previewError", { message: t("previewFailed") })
      )
    );
    expect(screen.getByRole("button", { name: t("grantCount", { count: 2 }) })).toBeDisabled();
    preview.mockRejectedValue(new Error("ACTUAL_BACKEND_MESSAGE"));
    fireEvent.click(screen.getByRole("button", { name: t("retry") }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("ACTUAL_BACKEND_MESSAGE")
    );
  });

  it("displays a civil expiry date unchanged across provider timezones", () => {
    const props = {
      initialDraft: { ...draft, expiry: { kind: "date" as const, date: "2026-10-31" } },
      initialStep: 4 as const,
      initialPreview: PREVIEW,
    };
    const { rerender } = render(ui(props, "Pacific/Kiritimati"));
    const expected = t("expiresDate", {
      date: new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeZone: "UTC" }).format(
        new Date("2026-10-31T00:00:00Z")
      ),
    });
    expect(
      screen.getByText(
        (_, node) => node?.tagName === "P" && node.textContent?.includes(expected) === true
      )
    ).toBeVisible();
    rerender(ui(props, "America/Adak"));
    expect(
      screen.getByText(
        (_, node) => node?.tagName === "P" && node.textContent?.includes(expected) === true
      )
    ).toBeVisible();
  });

  it("preserves arguments and rich tags throughout every plural branch", () => {
    const contract = (nodes: MessageFormatElement[]): string[] =>
      [
        ...new Set(
          nodes.flatMap((node): string[] => {
            if (node.type === TYPE.literal || node.type === TYPE.pound) return [];
            if (node.type === TYPE.tag) return [`tag:${node.value}`, ...contract(node.children)];
            if (node.type === TYPE.plural || node.type === TYPE.select)
              return [
                `${node.type}:${node.value}`,
                ...Object.values(node.options).flatMap((option) => contract(option.value)),
              ];
            return [`${node.type}:${node.value}`];
          })
        ),
      ].sort();
    const leaves = (obj: Record<string, unknown>, prefix = ""): Record<string, string> =>
      Object.fromEntries(
        Object.entries(obj).flatMap(([key, value]) =>
          typeof value === "string"
            ? [[prefix + key, value]]
            : Object.entries(leaves(value as Record<string, unknown>, `${prefix}${key}.`))
        )
      );
    const english = leaves(en.shared.access.grant);
    const actual = leaves(messages.shared.access.grant);
    expect(Object.keys(actual)).toEqual(Object.keys(english));
    for (const key of Object.keys(english))
      expect(contract(parse(actual[key]))).toEqual(contract(parse(english[key])));
  });
});

it("keeps a changed draft and exact submit identity when the locale changes", async () => {
  const submit = vi.fn().mockResolvedValue([{ principal: DANA, ok: true }]);
  const view = (locale: "es" | "ja") => (
    <NextIntlClientProvider locale={locale} messages={catalogs[locale]} timeZone="UTC">
      <GrantAccessFlow
        {...defaults}
        initialDraft={{ ...draft, principals: [DANA] }}
        initialStep={3}
        onSubmit={submit}
      />
    </NextIntlClientProvider>
  );
  const { rerender } = render(view("es"));
  const esT = createTranslator({ locale: "es", messages: es, namespace: "shared.access.grant" });
  fireEvent.click(screen.getByRole("button", { name: esT("days", { count: 7 }) }));
  rerender(view("ja"));
  const jaT = createTranslator({ locale: "ja", messages: ja, namespace: "shared.access.grant" });
  expect(screen.getByRole("button", { name: jaT("days", { count: 7 }) })).toHaveAttribute(
    "aria-pressed",
    "true"
  );
  await act(async () => fireEvent.click(screen.getByRole("button", { name: jaT("continue") })));
  fireEvent.click(screen.getByRole("button", { name: jaT("grantCount", { count: 1 }) }));
  await waitFor(() =>
    expect(submit).toHaveBeenCalledExactlyOnceWith({
      ...draft,
      principals: [DANA],
      expiry: { kind: "days", days: 7 },
    })
  );
});

it("hydrates the French date review across server/browser timezone settings without changing expiry", async () => {
  const view = (timeZone: string) => (
    <NextIntlClientProvider locale="fr" messages={fr} timeZone={timeZone}>
      <GrantAccessFlow
        {...defaults}
        initialDraft={{ ...draft, expiry: { kind: "date", date: "2026-10-31" } }}
        initialStep={4}
        initialPreview={PREVIEW}
      />
    </NextIntlClientProvider>
  );
  const container = document.createElement("div");
  document.body.appendChild(container);
  container.innerHTML = renderToString(view("Pacific/Kiritimati"));
  const before = container.textContent;
  const onRecoverableError = vi.fn();
  let root: ReturnType<typeof hydrateRoot>;
  await act(async () => {
    root = hydrateRoot(container, view("America/Adak"), { onRecoverableError });
  });
  expect(onRecoverableError).not.toHaveBeenCalled();
  expect(container.textContent).toBe(before);
  expect(container).toHaveTextContent("31 oct. 2026");
  await act(async () => root.unmount());
  container.remove();
});

it("does not report an empty write result as a successful grant", async () => {
  const done = vi.fn();
  render(
    <NextIntlClientProvider locale="es" messages={es} timeZone="UTC">
      <GrantAccessFlow
        {...defaults}
        initialDraft={draft}
        initialStep={4}
        initialPreview={PREVIEW}
        onSubmit={async () => []}
        onDone={done}
      />
    </NextIntlClientProvider>
  );
  const t = createTranslator({ locale: "es", messages: es, namespace: "shared.access.grant" });
  fireEvent.click(screen.getByRole("button", { name: t("grantCount", { count: 2 }) }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(t("noGrants")));
  expect(screen.getByRole("heading", { name: t("step.review") })).toBeVisible();
  expect(done).not.toHaveBeenCalled();
});
