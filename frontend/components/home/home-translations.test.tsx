import { readFileSync } from "node:fs";
import path from "node:path";
import { parse, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { BOTH, homeProps, NO_ACCESS, ONLY_APPS } from "./fixtures";
import { HomeScreen } from "./HomeScreen";
import { homePanelTitle, HOME_PANELS, localizedHomeLayout, HOME_LAYOUTS } from "./registry";
const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8")),
  ])
);
function leaves(value: Record<string, unknown>, prefix = ""): Record<string, string> {
  return Object.fromEntries(
    Object.entries(value).flatMap(([key, v]) => {
      const name = prefix ? `${prefix}.${key}` : key;
      return typeof v === "string"
        ? [[name, v]]
        : Object.entries(leaves(v as Record<string, unknown>, name));
    })
  );
}
function shape(nodes: MessageFormatElement[]): string[] {
  return [
    ...new Set(
      nodes.flatMap((node): string[] => {
        if (node.type === 0 || node.type === 7) return [];
        if (node.type === 8) return [`tag:${node.value}`, ...shape(node.children)];
        if (node.type === 5 || node.type === 6)
          return [
            `${node.type}:${node.value}`,
            ...Object.values(node.options).flatMap((o) => shape(o.value)),
          ];
        return [`${node.type}:${node.value}`];
      })
    ),
  ].sort();
}
function provider(locale: string, element: React.ReactNode) {
  const onError = vi.fn();
  const view = render(
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      timeZone="America/New_York"
      onError={onError}
    >
      {element}
    </NextIntlClientProvider>
  );
  return { ...view, onError };
}
describe("Home localization", () => {
  it.each(locales)("%s preserves all ICU argument and tag contracts", (locale) => {
    const canonical = leaves(catalogs.en.home),
      translated = leaves(catalogs[locale].home);
    expect(Object.keys(translated).sort()).toEqual(Object.keys(canonical).sort());
    for (const [key, value] of Object.entries(canonical))
      expect(shape(parse(translated[key]))).toEqual(shape(parse(value)));
  });
  it.each(locales)("%s asks before mounting panels and submits the actual layout key", (locale) => {
    const messages = catalogs[locale].home,
      mounted: string[] = [],
      onLayoutChange = vi.fn(),
      props = homeProps(BOTH);
    const view = provider(
      locale,
      <HomeScreen
        {...props}
        firstSignIn
        onLayoutChange={onLayoutChange}
        panels={props.panels.map((p) => ({
          ...p,
          component: ({ panel }) => {
            mounted.push(panel.key);
            return null;
          },
        }))}
      />
    );
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(messages.title);
    expect(screen.getByRole("group", { name: messages.question })).toBeInTheDocument();
    expect(mounted).toEqual([]);
    const radio = screen
      .getAllByRole("radio")
      .find((r) => (r as HTMLInputElement).value === "agents")!;
    expect(radio.closest("label")).toHaveTextContent(messages.layouts.agents.description);
    expect(radio.closest("label")).toHaveTextContent(messages.panels["failed-runs"]);
    fireEvent.click(radio);
    fireEvent.click(screen.getByRole("button", { name: messages.continue }));
    expect(onLayoutChange).toHaveBeenCalledExactlyOnceWith("agents");
    expect(view.onError).not.toHaveBeenCalled();
  });
  it.each(locales)("%s preserves offered access, hidden panels and caller labels", (locale) => {
    const props = homeProps(ONLY_APPS, "builder"),
      mounted: string[] = [],
      m = catalogs[locale].home;
    const view = provider(
      locale,
      <HomeScreen
        {...props}
        onLayoutChange={vi.fn()}
        panels={props.panels.map((p) => ({
          ...p,
          component: ({ panel }) => {
            mounted.push(panel.key);
            return <span>{panel.href}</span>;
          },
        }))}
      />
    );
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      m.titleLayout.replace("{layout}", m.layouts.apps.title)
    );
    expect(mounted).toEqual([
      "waiting",
      "failing",
      "my-apps",
      "recent-deployments",
      "traffic-errors",
    ]);
    expect(screen.queryByText("/tasks?view=failed")).not.toBeInTheDocument();
    view.unmount();
    const empty = provider(
      locale,
      <HomeScreen {...homeProps(NO_ACCESS)} onLayoutChange={vi.fn()} />
    );
    expect(screen.getByText(m.noAccessDescription)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: m.layoutMenu })).not.toBeInTheDocument();
    expect(empty.onError).not.toHaveBeenCalled();
    const t = (key: string) => key;
    expect(homePanelTitle({ ...HOME_PANELS.waiting, title: "EXACT_CALLER_TITLE" }, t)).toBe(
      "EXACT_CALLER_TITLE"
    );
    expect(
      localizedHomeLayout(
        { ...HOME_LAYOUTS.apps, title: "CALLER_LAYOUT", description: "CALLER_DESCRIPTION" },
        t
      )
    ).toMatchObject({
      title: "CALLER_LAYOUT",
      description: "CALLER_DESCRIPTION",
      panels: HOME_LAYOUTS.apps.panels,
      requires: HOME_LAYOUTS.apps.requires,
    });
  });
});
