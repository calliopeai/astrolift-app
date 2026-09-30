import { parse, TYPE, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import de from "@/messages/de.json";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";

import {
  localizedBindReason,
  localizedPermissionPresentation,
  localizedResourceLabel,
} from "./access-copy";
import { canBindAt, summarizePermissions, type RoleRef, type ScopeNode } from "./access-model";
import { PermissionMatrix } from "./PermissionMatrix";
import { PermissionPicker } from "./PermissionPicker";
import { RoleSummary } from "./RoleSummary";
import { ScopePicker } from "./ScopePicker";

const catalogs = { en, es, fr, de, "pt-BR": pt, ja, ko, "zh-Hans": zh };
const slugs = [
  "app.read",
  "app.deploy",
  "app.delete",
  "secret.read",
  "provider_opaque_id.raw_verb",
];
const role: RoleRef = {
  id: "role-id",
  name: "CUSTOM_ROLE_NAME",
  slug: "custom-role-slug",
  scopeLevel: "APP",
  permissions: ["app.read", "app.deploy"],
  isSystem: false,
};
const app: ScopeNode = {
  kind: "APP",
  id: "actual-app-id",
  name: "app:<literal>{name}",
  slug: "actual-app-slug",
};
const team: ScopeNode = {
  kind: "TEAM",
  id: "actual-team-id",
  name: "Payments",
  hasChildren: true,
  children: [app],
};
const org: ScopeNode = { kind: "ORG", id: "actual-org-id", name: "Acme", children: [team] };

describe.each(Object.entries(catalogs))("%s access controls", (locale, messages) => {
  const t = createTranslator({ locale, messages, namespace: "shared.access" });
  const provider = (children: ReactNode) => (
    <NextIntlClientProvider locale={locale} messages={messages}>
      {children}
    </NextIntlClientProvider>
  );

  it("translates role chrome/fallback and expansion while preserving role metadata and actual slugs", () => {
    const { rerender } = render(
      provider(
        <RoleSummary role={{ ...role, description: "SERVER_ROLE_DESCRIPTION" }} catalog={slugs} />
      )
    );
    expect(screen.getByText(role.name)).toBeInTheDocument();
    expect(screen.getByText(role.slug)).toBeInTheDocument();
    expect(screen.getByText("SERVER_ROLE_DESCRIPTION")).toBeInTheDocument();
    expect(screen.getByText(t("role.custom"))).toBeInTheDocument();
    expect(screen.getByText(t("role.count", { count: 2 }))).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: t("role.show") }));
    expect(screen.getByRole("table", { name: t("matrix.label") })).toBeInTheDocument();
    expect(
      screen.getByRole("img", {
        name: t("matrix.stateLabel", { slug: "app.read", state: t("matrix.state.on") }),
      })
    ).toBeInTheDocument();
    expect(screen.getByText("deploy").parentElement).toHaveAttribute(
      "title",
      t("matrix.stateTitle", { slug: "app.deploy", state: t("matrix.state.on") })
    );
    fireEvent.click(screen.getByRole("button", { name: t("role.hide") }));
    expect(screen.queryByRole("table")).toBeNull();
    rerender(
      provider(<RoleSummary role={{ ...role, permissions: [], isSystem: true }} catalog={slugs} />)
    );
    expect(screen.getByText(t("role.builtIn"))).toBeInTheDocument();
    expect(screen.getByText(t("presentation.none"))).toBeInTheDocument();
    expect(screen.getByText(t("role.count", { count: 0 }))).toBeInTheDocument();
    rerender(
      provider(
        <RoleSummary
          role={{ ...role, permissions: ["app.read"], isSystem: true }}
          catalog={slugs}
        />
      )
    );
    expect(screen.getByText(t("role.count", { count: 1 }))).toBeInTheDocument();
    expect(
      screen.getByText(t("presentation.readOnly", { resources: t("presentation.resource.app") }))
    ).toBeInTheDocument();
  });

  it("keeps cell, whole-resource and whole-area changes on canonical permission slugs, with localized diff states", () => {
    const change = vi.fn();
    const { rerender } = render(
      provider(
        <PermissionMatrix
          permissions={["app.read"]}
          catalog={slugs}
          base={["app.delete"]}
          baseLabel="SERVER_BASE_ROLE"
          onChange={change}
          defaultOpen={["apps", "other"]}
        />
      )
    );
    expect(screen.getByText(t("matrix.vs", { name: "SERVER_BASE_ROLE" }))).toBeInTheDocument();
    expect(
      screen.getByTitle(
        t("matrix.stateTitle", { slug: "app.read", state: t("matrix.state.added") })
      )
    ).toBeInTheDocument();
    expect(
      screen.getByTitle(
        t("matrix.stateTitle", { slug: "app.delete", state: t("matrix.state.removed") })
      )
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "app.deploy" }));
    expect(change).toHaveBeenLastCalledWith(["app.deploy", "app.read"]);
    fireEvent.click(
      screen
        .getAllByRole("checkbox", {
          name: t("matrix.all", { name: t("presentation.resource.app") }),
        })
        .find((node) => node.closest('[role="row"]')?.querySelector('[title="app"]'))!
    );
    expect(change).toHaveBeenLastCalledWith(["app.delete", "app.deploy", "app.read"]);
    fireEvent.click(
      screen
        .getAllByRole("checkbox", { name: t("matrix.all", { name: t("presentation.area.apps") }) })
        .find((node) => node.closest('[role="row"]')?.querySelector("button[aria-expanded]"))!
    );
    expect(change).toHaveBeenLastCalledWith([
      "app.delete",
      "app.deploy",
      "app.read",
      "secret.read",
    ]);
    expect(screen.getByText("provider_opaque_id")).toHaveAttribute("title", "provider_opaque_id");
    fireEvent.change(screen.getByRole("searchbox", { name: t("matrix.filter") }), {
      target: { value: "NONMATCH" },
    });
    expect(screen.getByText(t("matrix.noMatch", { query: "NONMATCH" }))).toBeInTheDocument();
    rerender(provider(<PermissionMatrix permissions={[]} catalog={[]} />));
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
    expect(screen.getByText(t("matrix.notLoaded"))).toBeInTheDocument();
  });

  it("filters and selects exact permission slugs and keeps a caller's custom field label", () => {
    const change = vi.fn();
    const { rerender } = render(
      provider(<PermissionPicker value="app.read" onChange={change} catalog={slugs} />)
    );
    expect(screen.getByRole("group", { name: t("permissionPicker.label") })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "app.read" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    fireEvent.change(screen.getByRole("searchbox", { name: t("matrix.filter") }), {
      target: { value: "provider_opaque_id" },
    });
    expect(screen.getByText("provider_opaque_id")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "app.read" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "provider_opaque_id.raw_verb" }));
    expect(change).toHaveBeenLastCalledWith("provider_opaque_id.raw_verb");
    rerender(
      provider(
        <PermissionPicker value={null} onChange={change} catalog={[]} label="CUSTOM_FIELD_LABEL" />
      )
    );
    expect(screen.getByRole("group", { name: "CUSTOM_FIELD_LABEL" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
    expect(screen.getByText(t("permissionPicker.empty"))).toBeInTheDocument();
  });

  it("keeps disabled scopes unpickable by click/keyboard and preserves selected nodes/IDs, raw reasons and retry", () => {
    const change = vi.fn();
    const retry = vi.fn();
    const { rerender } = render(
      provider(
        <ScopePicker
          roots={[org]}
          value={app}
          onChange={change}
          selectable={(node) => canBindAt(role, node.kind, localizedBindReason(t))}
        />
      )
    );
    const tree = screen.getByRole("tree", { name: t("scopePicker.label") });
    const rootRow = screen.getByRole("treeitem", { name: /Acme/ });
    expect(rootRow).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(rootRow);
    fireEvent.focus(rootRow);
    fireEvent.keyDown(tree, { key: "Enter" });
    expect(change).not.toHaveBeenCalled();
    expect(
      screen.getAllByText(t("presentation.bindReason", { name: role.name, kind: t("scope.APP") }))
        .length
    ).toBeGreaterThan(0);
    const appRow = screen.getByRole("treeitem", { name: /app:<literal>\{name\}/ });
    expect(appRow).toHaveAttribute("data-key", "APP:actual-app-id");
    expect(appRow).toHaveAttribute("aria-selected", "true");
    fireEvent.click(appRow);
    expect(change).toHaveBeenLastCalledWith(app);
    fireEvent.focus(appRow);
    fireEvent.keyDown(tree, { key: "Enter" });
    expect(change).toHaveBeenCalledTimes(2);
    rerender(
      provider(
        <ScopePicker
          roots={[org]}
          value={app}
          onChange={change}
          error={{ message: "RAW_BACKEND_DIAGNOSTIC" }}
          onRetry={retry}
        />
      )
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      t("scopePicker.failed", { message: "RAW_BACKEND_DIAGNOSTIC" })
    );
    fireEvent.click(screen.getByRole("button", { name: t("scopePicker.retry") }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it("translates pure presentation opt-ins without altering stock helper behavior or unknown IDs/verbs", () => {
    const presentation = localizedPermissionPresentation(t, (items) =>
      new Intl.ListFormat(locale).format(items)
    );
    expect(localizedResourceLabel("opaque__identifier", t)).toBe("opaque__identifier");
    expect(summarizePermissions([], slugs, presentation)).toBe(t("presentation.none"));
    expect(summarizePermissions(slugs, slugs, presentation)).toBe(t("presentation.everything"));
    expect(summarizePermissions(["opaque__identifier.raw_verb"], slugs, presentation)).toContain(
      "opaque__identifier"
    );
    expect(summarizePermissions(["opaque__identifier.raw_verb"], slugs, presentation)).toContain(
      "raw_verb"
    );
    expect(summarizePermissions([], slugs)).toBe("No permissions");
    expect(canBindAt(role, "TEAM")).toMatch(/app role/);
    expect(canBindAt(role, "TEAM", localizedBindReason(t))).toBe(
      t("presentation.bindReason", { name: role.name, kind: t("scope.APP") })
    );
    expect(canBindAt(role, "APP", localizedBindReason(t))).toBe(true);
  });

  it("preserves translated message argument, plural and rich-tag contracts", () => {
    function leaves(data: Record<string, unknown>): Record<string, string> {
      return Object.fromEntries(
        Object.entries(data).flatMap(([key, value]) =>
          typeof value === "string"
            ? [[key, value]]
            : Object.entries(leaves(value as Record<string, unknown>)).map(([child, text]) => [
                `${key}.${child}`,
                text,
              ])
        )
      );
    }
    function contract(nodes: MessageFormatElement[]): string[] {
      return [
        ...new Set(
          nodes.flatMap((node): string[] => {
            if (node.type === TYPE.literal) return [];
            if (node.type === TYPE.tag) return [`tag:${node.value}`, ...contract(node.children)];
            if (node.type === TYPE.plural)
              return [`plural:${node.value}`, ...contract(node.options.other.value)];
            if (node.type === TYPE.select)
              return [
                `select:${node.value}`,
                ...Object.values(node.options).flatMap((x) => contract(x.value)),
              ];
            return [node.type === TYPE.pound ? "pound" : `${node.type}:${node.value}`];
          })
        ),
      ].sort();
    }
    const roots = ["role", "matrix", "permissionPicker", "scopePicker", "presentation"] as const;
    for (const root of roots) {
      const english = leaves(en.shared.access[root]);
      const actual = leaves(messages.shared.access[root]);
      expect(Object.keys(actual).sort()).toEqual(Object.keys(english).sort());
      for (const key of Object.keys(english))
        expect(contract(parse(actual[key]))).toEqual(contract(parse(english[key])));
    }
  });
});

it("keeps server lazy-load diagnostics and uses the current locale for an unknown-error fallback without changing load targets", async () => {
  const lazy = { ...team, children: undefined };
  const load = vi.fn().mockRejectedValue("not-an-error-object");
  const ui = (locale: "en" | "ja") => (
    <NextIntlClientProvider locale={locale} messages={catalogs[locale]}>
      <ScopePicker roots={[lazy]} value={null} onChange={() => {}} loadChildren={load} />
    </NextIntlClientProvider>
  );
  const { rerender } = render(ui("en"));
  fireEvent.click(screen.getByRole("button", { name: "Collapse Payments" }));
  fireEvent.click(screen.getByRole("button", { name: "Expand Payments" }));
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent(en.shared.access.scopePicker.loadFailed)
  );
  expect(load).toHaveBeenLastCalledWith(lazy);
  rerender(ui("ja"));
  expect(screen.getByRole("status")).toHaveTextContent(ja.shared.access.scopePicker.loadFailed);
  load.mockRejectedValue(new Error("RAW_SERVER_FAILURE"));
  fireEvent.click(
    screen.getByRole("button", {
      name: ja.shared.access.scopePicker.collapse.replace("{name}", "Payments"),
    })
  );
  await act(async () =>
    fireEvent.click(
      screen.getByRole("button", {
        name: ja.shared.access.scopePicker.expand.replace("{name}", "Payments"),
      })
    )
  );
  expect(screen.getByRole("status")).toHaveTextContent("RAW_SERVER_FAILURE");
  expect(load).toHaveBeenCalledTimes(2);
});
