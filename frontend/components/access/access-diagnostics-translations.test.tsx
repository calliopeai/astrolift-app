import { parse, TYPE, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { fireEvent, render, screen, within } from "@testing-library/react";
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
  AccessCompare,
  AccessExplainer,
  parseBindingLabels,
  type Diagnosis,
} from "./AccessExplainer";

const catalogs = { en, es, fr, de, "pt-BR": pt, ja, ko, "zh-Hans": zh };
const literal = "operator:<literal>{username}";
const target = { kind: "APP" as const, id: "actual-app-id", name: "checkout:<literal>{name}" };
const diagnosis: Diagnosis = {
  username: literal,
  permission: "app.deploy",
  granted: false,
  isSuperuser: false,
  steps: [
    { check: "is_active", result: true, detail: "RAW_ACTIVE_DETAIL" },
    { check: "is_superuser", result: false, detail: "RAW_SUPERUSER_DETAIL" },
    {
      check: "bindings_carrying_this_permission",
      result: true,
      detail: "custom-role@TEAM:actual-team-id, inherited-role@ORG:actual-org-id",
    },
    {
      check: "abac_policies",
      result: false,
      detail: "RAW_POLICY_DIAGNOSTIC: deny app.deploy <script>literal</script>",
    },
    { check: "unknown_check_id", result: false, detail: "RAW_UNKNOWN_DETAIL" },
    { check: "resolver_verdict", result: false, detail: "RAW_DENIED_DETAIL" },
  ],
};

const flatten = (obj: Record<string, unknown>): Record<string, string> =>
  Object.fromEntries(
    Object.entries(obj).flatMap(([key, value]) =>
      typeof value === "string"
        ? [[key, value]]
        : Object.entries(flatten(value as Record<string, unknown>)).map(([child, text]) => [
            `${key}.${child}`,
            text,
          ])
    )
  );
const contract = (nodes: MessageFormatElement[]): string[] =>
  [
    ...new Set(
      nodes.flatMap((node): string[] => {
        if (node.type === TYPE.literal) return [];
        if (node.type === TYPE.tag) return [`tag:${node.value}`, ...contract(node.children)];
        if (node.type === TYPE.plural || node.type === TYPE.select)
          return [
            `${node.type}:${node.value}`,
            ...Object.values(node.options).flatMap((option) => contract(option.value)),
          ];
        return [node.type === TYPE.pound ? "pound" : `${node.type}:${node.value}`];
      })
    ),
  ].sort();

describe.each(Object.entries(catalogs))("%s access diagnostics", (locale, messages) => {
  const t = createTranslator({ locale, messages, namespace: "shared.access.diagnostics" });
  const provider = (children: ReactNode) => (
    <NextIntlClientProvider locale={locale} messages={messages}>
      {children}
    </NextIntlClientProvider>
  );

  it("localizes a policy-denied verdict and reasoning while retaining resolver details and exact binding links", () => {
    const bindingHref = vi.fn(
      (binding) => `/administration/access/${binding.scopeKind}/${binding.scopeId}/${binding.role}`
    );
    const { container } = render(
      provider(<AccessExplainer diagnosis={diagnosis} target={target} bindingHref={bindingHref} />)
    );
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(t("answer.denied"));
    expect(status).not.toHaveTextContent(t("answer.granted"));
    expect(within(status).getByText(literal)).toBeInTheDocument();
    expect(within(status).getByText("app.deploy")).toBeInTheDocument();
    expect(within(status).getByText(target.name)).toBeInTheDocument();
    expect(screen.getByRole("list", { name: t("reasoning") })).toBeInTheDocument();
    expect(
      screen.getByText(t("step.abac_policies"), { exact: false }).closest("p")
    ).toHaveTextContent(t("tone.fail"));
    expect(
      screen.getByText(
        (_, element) =>
          element?.tagName === "P" && element.firstChild?.textContent === t("step.is_superuser")
      )
    ).toHaveTextContent(t("tone.info"));
    expect(screen.getByText("unknown_check_id", { exact: false })).toHaveTextContent(
      "unknown_check_id"
    );
    expect(screen.getByText(diagnosis.steps[3].detail)).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByRole("link", { name: "custom-role@TEAM:actual-team-id" })).toHaveAttribute(
      "href",
      "/administration/access/TEAM/actual-team-id/custom-role"
    );
    expect(screen.getByRole("link", { name: "inherited-role@ORG:actual-org-id" })).toHaveAttribute(
      "href",
      "/administration/access/ORG/actual-org-id/inherited-role"
    );
    expect(bindingHref.mock.calls.map(([value]) => value)).toEqual([
      { role: "custom-role", scopeKind: "TEAM", scopeId: "actual-team-id" },
      { role: "inherited-role", scopeKind: "ORG", scopeId: "actual-org-id" },
    ]);
  });

  it("renders an org-wide positive superuser result from the supplied answer without fabricating a target", () => {
    render(
      provider(
        <AccessExplainer
          diagnosis={{ ...diagnosis, granted: true, isSuperuser: true, steps: [] }}
        />
      )
    );
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(t("answer.granted"));
    expect(status).toHaveTextContent(t("superuser"));
    expect(within(status).queryByText(target.name)).toBeNull();
    expect(status).not.toHaveTextContent("actual-app-id");
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("keeps retry callbacks and raw error diagnostics, and retains truthful empty/loading states", () => {
    const retry = vi.fn();
    const { rerender, container } = render(
      provider(
        <AccessExplainer
          diagnosis={null}
          error={{ message: "RAW_FORBIDDEN_DIAGNOSTIC" }}
          onRetry={retry}
        />
      )
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      t("failed", { message: "RAW_FORBIDDEN_DIAGNOSTIC" })
    );
    fireEvent.click(screen.getByRole("button", { name: t("retry") }));
    expect(retry).toHaveBeenCalledTimes(1);
    rerender(provider(<AccessExplainer diagnosis={null} loading />));
    expect(container.querySelector('[aria-busy="true"]')).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
    rerender(provider(<AccessExplainer diagnosis={null} />));
    expect(screen.getByText(t("prompt"))).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("translates comparisons while preserving exclusive/shared partitions, literal usernames and permission IDs", () => {
    const { rerender } = render(
      provider(
        <AccessCompare
          comparison={{
            userAUsername: literal,
            userBUsername: "SECOND_USER_LITERAL",
            onlyA: [],
            onlyB: ["private_opaque_id.raw_verb"],
            shared: ["app.read"],
          }}
        />
      )
    );
    const headers = screen.getAllByRole("heading", { level: 3 });
    expect(headers).toHaveLength(3);
    expect(headers[0]).toHaveTextContent(literal);
    expect(headers[1]).toHaveTextContent("SECOND_USER_LITERAL");
    expect(headers[2]).toHaveTextContent(t("compare.both"));
    expect(within(headers[0].closest("section")!).getByText(t("compare.none"))).toBeInTheDocument();
    expect(
      within(headers[1].closest("section")!).getByText("private_opaque_id.raw_verb")
    ).toHaveAttribute("title", "private_opaque_id.raw_verb");
    expect(within(headers[2].closest("section")!).getByText("app.read")).toBeInTheDocument();
    expect(
      within(headers[2].closest("section")!).queryByText("private_opaque_id.raw_verb")
    ).toBeNull();
    rerender(provider(<AccessCompare comparison={null} />));
    expect(screen.getByText(t("compare.prompt"))).toBeInTheDocument();
    rerender(
      provider(<AccessCompare comparison={null} error={{ message: "RAW_COMPARE_ERROR" }} />)
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      t("compare.failed", { message: "RAW_COMPARE_ERROR" })
    );
  });

  it("retains rich-tag and argument contracts across localized sentence reordering", () => {
    const actual = flatten(messages.shared.access.diagnostics);
    const english = flatten(en.shared.access.diagnostics);
    expect(Object.keys(actual)).toEqual(Object.keys(english));
    for (const key of Object.keys(english))
      expect(contract(parse(actual[key]))).toEqual(contract(parse(english[key])));
  });
});

it("parses only coherent backend binding labels without guessing targets from diagnostics", () => {
  expect(parseBindingLabels("ROLE@APP:actual-app-id")).toEqual([
    { role: "ROLE", scopeKind: "APP", scopeId: "actual-app-id" },
  ]);
  expect(parseBindingLabels("ROLE@UNKNOWN:actual-app-id")).toBeNull();
  expect(parseBindingLabels("RAW_UNPARSEABLE_DIAGNOSTIC")).toBeNull();
  expect(parseBindingLabels("ROLE@APP:actual-app-id, raw detail")).toBeNull();
});
