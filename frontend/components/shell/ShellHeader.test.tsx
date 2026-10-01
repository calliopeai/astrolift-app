import { act, render, screen, waitFor, within } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { userEvent } from "storybook/test";
import { describe, expect, it, vi } from "vitest";

import de from "@/messages/de.json";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";

import { ShellHeader, type Crumb } from "./ShellHeader";

const catalogs = { en, es, fr, de, "pt-BR": pt, ja, ko, "zh-Hans": zh };
const project = "CUSTOM_PROJECT <literal>{label}";
const other = "CUSTOM_OTHER <literal>{name}";
const app = "CUSTOM_APP <literal>{name}";
const detail = "CUSTOM_DETAIL <literal>{name}";
const title = "CUSTOM_TITLE <literal>{name}";
const crumbs: Crumb[] = [
  {
    label: project,
    switcher: [
      { label: project, href: "/projects/actual", active: true },
      { label: other, href: "/projects/other" },
    ],
  },
  { label: app, href: "/apps/actual" },
  { label: detail, href: "/apps/actual/detail" },
];
const ui = (locale: keyof typeof catalogs, onError = () => {}) => (
  <NextIntlClientProvider
    locale={locale}
    messages={catalogs[locale]}
    timeZone="UTC"
    onError={onError}
  >
    <ShellHeader
      title={title}
      crumbs={crumbs}
      tabs={[
        {
          key: "technical-tab-key",
          label: "CUSTOM_TAB",
          href: "/apps/actual/settings",
          active: true,
        },
      ]}
      tabsAriaLabel="CUSTOM_TABS_LABEL"
    />
  </NextIntlClientProvider>
);

describe.each(Object.entries(catalogs))("%s breadcrumb accessibility", (locale, messages) => {
  const t = createTranslator({ locale, messages, namespace: "shared.shellHeader" });

  it("names the landmark and switch purpose in the locale while retaining links, labels, active state and keyboard focus", async () => {
    const onError = vi.fn();
    render(ui(locale as keyof typeof catalogs, onError));
    const nav = screen.getByRole("navigation", { name: t("breadcrumb") });
    const trigger = within(nav).getByRole("button", { name: t("switch", { label: project }) });
    expect(screen.getByRole("heading", { name: title })).toBeVisible();
    expect(within(nav).getByRole("link", { name: app })).toHaveAttribute("href", "/apps/actual");
    expect(within(nav).getByText(detail)).toHaveAttribute("aria-current", "page");
    expect(within(nav).queryByRole("link", { name: detail })).toBeNull();
    expect(screen.getByRole("navigation", { name: "CUSTOM_TABS_LABEL" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "CUSTOM_TAB" })).toHaveAttribute(
      "href",
      "/apps/actual/settings"
    );
    trigger.focus();
    await userEvent.keyboard("{Enter}");
    const menu = await screen.findByRole("menu");
    expect(within(menu).getByRole("menuitem", { name: project })).toHaveAttribute(
      "aria-current",
      "page"
    );
    const destination = within(menu).getByRole("menuitem", { name: other });
    expect(destination).toHaveAttribute("href", "/projects/other");
    expect(destination).not.toHaveAttribute("aria-current");
    await userEvent.keyboard("{End}");
    await waitFor(() => expect(destination).toHaveFocus());
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(screen.queryByRole("menu")).toBeNull();
    expect(onError).not.toHaveBeenCalled();
    if (locale !== "en") {
      expect(t("breadcrumb")).not.toBe(en.shared.shellHeader.breadcrumb);
      expect(t("switch", { label: project })).not.toBe(`${project}: switch`);
    }
  });

  it("hydrates actual breadcrumb controls with matching localized accessible names and working menus", async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const tree = ui(locale as keyof typeof catalogs);
    container.innerHTML = renderToString(tree, { identifierPrefix: "localized-header" });
    const before = container.innerHTML;
    const onRecoverableError = vi.fn();
    let root: ReturnType<typeof hydrateRoot>;
    await act(async () => {
      root = hydrateRoot(container, tree, {
        identifierPrefix: "localized-header",
        onRecoverableError,
      });
    });
    try {
      expect(onRecoverableError).not.toHaveBeenCalled();
      expect(container.innerHTML).toBe(before);
      const nav = within(container).getByRole("navigation", { name: t("breadcrumb") });
      const trigger = within(nav).getByRole("button", { name: t("switch", { label: project }) });
      await userEvent.click(trigger);
      expect(await screen.findByRole("menuitem", { name: other })).toHaveAttribute(
        "href",
        "/projects/other"
      );
      await userEvent.keyboard("{Escape}");
      await waitFor(() => expect(trigger).toHaveFocus());
    } finally {
      await act(async () => root.unmount());
      container.remove();
    }
  });
});

it("updates accessibility copy on locale change without changing an open menu's destinations or caller labels", async () => {
  const { rerender } = render(ui("es"));
  const esT = createTranslator({ locale: "es", messages: es, namespace: "shared.shellHeader" });
  const trigger = screen.getByRole("button", { name: esT("switch", { label: project }) });
  const nav = screen.getByRole("navigation", { name: esT("breadcrumb") });
  await userEvent.click(trigger);
  const original = screen.getByRole("menuitem", { name: project });
  rerender(ui("ja"));
  const jaT = createTranslator({ locale: "ja", messages: ja, namespace: "shared.shellHeader" });
  expect(trigger).toHaveAttribute("aria-label", jaT("switch", { label: project }));
  expect(trigger).toHaveAttribute("aria-expanded", "true");
  expect(nav).toHaveAttribute("aria-label", jaT("breadcrumb"));
  expect(screen.getByRole("menuitem", { name: project })).toBe(original);
  expect(original).toHaveAttribute("href", "/projects/actual");
  expect(original).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("menuitem", { name: other })).toHaveAttribute("href", "/projects/other");
  await userEvent.keyboard("{Escape}");
  expect(screen.getByRole("button", { name: jaT("switch", { label: project }) })).toBe(trigger);
  expect(screen.getByRole("navigation", { name: jaT("breadcrumb") })).toBe(nav);
});
