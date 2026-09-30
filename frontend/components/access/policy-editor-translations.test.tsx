import { parse, TYPE, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { fireEvent, render, screen } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import * as React from "react";
import { describe, expect, it, vi } from "vitest";

import de from "@/messages/de.json";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";

import { localizedConditionError } from "./policy-copy";
import {
  conditionError,
  parseCondition,
  serializePolicy,
  WEEKDAYS,
  type PolicyCondition,
  type PolicyShape,
} from "./policy-model";
import { PolicySentence } from "./PolicySentence";

const catalogs = { en, es, fr, de, "pt-BR": pt, ja, ko, "zh-Hans": zh };
const rawCondition = { kind: "future_raw_kind", opaque_property: ["COUNTRY_ID", { nested: true }] };
const policy: PolicyShape = {
  effect: "DENY",
  actionPattern: "app.deploy",
  resource: {
    app_slug: ["actual-app-id"],
    project_slug: ["actual-project-id"],
    env: ["production"],
    region: ["eu-west-1"],
  },
  actor: { groups: ["idp:<literal>{group}"], role: "RAW_ROLE_SLUG" },
  conditions: [
    {
      kind: "time_window",
      days: ["mon", "future_day_id"],
      hours: ["09:00-18:00"],
      tz: "Europe/Paris",
    },
    { kind: "ip_allowlist", cidrs: ["10.0.0.0/8"] },
    { kind: "approval_required", min_approvers: 2 },
    { kind: "env_match", env_in: ["staging", "preview"] },
    { kind: "device_assertion", required_factors: ["webauthn", "otp"] },
    { kind: "freshness", max_session_age_minutes: 15 },
    { kind: "custom", raw: rawCondition },
  ],
};
type ValidationKey = Parameters<Parameters<typeof localizedConditionError>[1]>[0];
const errors: [PolicyCondition, ValidationKey][] = [
  [{ kind: "time_window", days: [], hours: ["09:00-18:00"], tz: "UTC" }, "error.days"],
  [{ kind: "time_window", days: ["mon"], hours: [], tz: "UTC" }, "error.ranges"],
  [{ kind: "time_window", days: ["mon"], hours: ["invalid-hours"], tz: "UTC" }, "error.hours"],
  [{ kind: "time_window", days: ["mon"], hours: ["09:00-18:00"], tz: "" }, "error.zone"],
  [{ kind: "ip_allowlist", cidrs: [] }, "error.cidrs"],
  [{ kind: "ip_allowlist", cidrs: ["not-a-cidr"] }, "error.cidrFormat"],
  [{ kind: "approval_required", min_approvers: 0 }, "error.approver"],
  [{ kind: "env_match", env_in: [] }, "error.environment"],
  [{ kind: "device_assertion", required_factors: [] }, "error.factor"],
  [{ kind: "freshness", max_session_age_minutes: 0 }, "error.minute"],
];
function Editor({ onChange }: { onChange: (next: PolicyShape) => void }) {
  const [draft, setDraft] = React.useState(policy);
  return (
    <PolicySentence
      policy={draft}
      onChange={(next) => {
        setDraft(next);
        onChange(next);
      }}
    />
  );
}
const leaves = (obj: Record<string, unknown>): Record<string, string> =>
  Object.fromEntries(
    Object.entries(obj).flatMap(([key, value]) =>
      typeof value === "string"
        ? [[key, value]]
        : Object.entries(leaves(value as Record<string, unknown>)).map(([child, text]) => [
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
          return [`${node.type}:${node.value}`, ...contract(node.options.other.value)];
        return [node.type === TYPE.pound ? "pound" : `${node.type}:${node.value}`];
      })
    ),
  ].sort();

describe.each(Object.entries(catalogs))("%s policy sentence/editor", (locale, messages) => {
  const t = createTranslator({ locale, messages, namespace: "shared.access.policy" });
  const provider = (children: React.ReactNode) => (
    <NextIntlClientProvider locale={locale} messages={messages}>
      {children}
    </NextIntlClientProvider>
  );
  const conditionPhrase = (kind: "unless" | "onlyWhen") =>
    t.rich(`summary.${kind}`, { tests: () => "__TEST__" }) as string;
  const assertConditionPhrase = (element: HTMLElement, kind: "unless" | "onlyWhen") => {
    for (const part of conditionPhrase(kind)
      .split("__TEST__")
      .filter((part) => part.trim()))
      expect(element).toHaveTextContent(part.trim());
  };

  it("renders opposite DENY-unless/ALLOW-only-when semantics without changing literal policy values or future conditions", () => {
    const before = serializePolicy(policy);
    const { container, rerender } = render(provider(<PolicySentence policy={policy} />));
    const sentence = container.querySelector("p")!;
    expect(sentence).toHaveTextContent(t("deny"));
    assertConditionPhrase(sentence, "unless");
    for (const value of [
      "app.deploy",
      "actual-app-id",
      "actual-project-id",
      "production",
      "eu-west-1",
      "idp:<literal>{group}",
      "RAW_ROLE_SLUG",
      "future_day_id",
      "09:00-18:00",
      "Europe/Paris",
      "10.0.0.0/8",
      "webauthn",
      "otp",
      "future_raw_kind",
    ])
      expect(sentence).toHaveTextContent(value);
    expect(container.querySelector("script")).toBeNull();
    expect(serializePolicy(policy)).toEqual(before);
    rerender(provider(<PolicySentence policy={{ ...policy, effect: "ALLOW" }} />));
    expect(sentence).toHaveTextContent(t("allow"));
    assertConditionPhrase(sentence, "onlyWhen");
    rerender(
      provider(
        <PolicySentence
          policy={{
            ...policy,
            actionPattern: "*",
            resource: {},
            actor: { groups: [], role: "" },
            conditions: [],
          }}
        />
      )
    );
    expect(sentence).toHaveTextContent(t("summary.everyAction"));
    expect(sentence).toHaveTextContent(t("summary.everyone"));
    expect(sentence).toHaveTextContent(t("anything"));
    expect(sentence).not.toHaveTextContent("future_raw_kind");
  });

  it("keeps canonical effect/action/scope/weekday/factor edits and outgoing serialized keys, with translated editor labels", () => {
    const change = vi.fn();
    render(provider(<Editor onChange={change} />));
    fireEvent.change(screen.getByLabelText(t("action")), { target: { value: "secret.*" } });
    expect(change).toHaveBeenLastCalledWith({ ...policy, actionPattern: "secret.*" });
    fireEvent.click(screen.getByRole("combobox", { name: t("effect") }));
    fireEvent.click(screen.getByRole("option", { name: t("allow") }));
    expect(change.mock.lastCall![0].effect).toBe("ALLOW");
    fireEvent.change(screen.getByLabelText(t("timeZone")), {
      target: { value: "America/Costa_Rica" },
    });
    expect(change.mock.lastCall![0].conditions[0]).toEqual({
      ...policy.conditions[0],
      tz: "America/Costa_Rica",
    });
    const monday = new Intl.DateTimeFormat(locale, { weekday: "short", timeZone: "UTC" }).format(
      new Date(Date.UTC(2026, 8, 28))
    );
    fireEvent.click(screen.getByRole("button", { name: monday }));
    expect(change.mock.lastCall![0].conditions[0].days).toEqual(["future_day_id"]);
    fireEvent.click(screen.getByRole("button", { name: "hwk" }));
    expect(change.mock.lastCall![0].conditions[4].required_factors).toEqual([
      "webauthn",
      "otp",
      "hwk",
    ]);
    fireEvent.click(
      screen.getByRole("button", { name: t("removeMatch", { kind: t("resource.region") }) })
    );
    const serialized = serializePolicy(change.mock.lastCall![0]);
    expect(serialized.resourcePattern).toEqual({
      app_slug: "actual-app-id",
      project_slug: "actual-project-id",
      env: "production",
    });
    expect(serialized.actorPattern).toEqual({
      user_in_groups: ["idp:<literal>{group}"],
      user_role_at_scope: "RAW_ROLE_SLUG",
    });
    expect(serialized.conditions.at(-1)).toEqual(rawCondition);
    expect(serialized.actionPattern).toBe("secret.*");
    expect(serialized.effect).toBe("ALLOW");
    expect(WEEKDAYS).toEqual(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]);
  });

  it("keeps raw JSON drafts through shape refusal and retry, preserving unknown condition payloads", () => {
    const change = vi.fn();
    render(provider(<Editor onChange={change} />));
    fireEvent.click(screen.getByRole("button", { name: t("jsonEdit") }));
    const field = screen.getByLabelText(t("jsonLabel"));
    expect(JSON.parse((field as HTMLTextAreaElement).value).at(-1)).toEqual(rawCondition);
    fireEvent.change(field, { target: { value: '{"kind":"opaque_raw_kind"}' } });
    fireEvent.click(screen.getByRole("button", { name: t("apply") }));
    expect(field).toHaveValue('{"kind":"opaque_raw_kind"}');
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText(t("jsonArray"))).toBeInTheDocument();
    expect(change).not.toHaveBeenCalled();
    const raw = [{ kind: "unknown_kind_id", future_payload: { raw_key: "id:<literal>{value}" } }];
    fireEvent.change(field, { target: { value: JSON.stringify(raw) } });
    fireEvent.click(screen.getByRole("button", { name: t("apply") }));
    expect(change).toHaveBeenLastCalledWith({ ...policy, conditions: raw.map(parseCondition) });
    expect(serializePolicy(change.mock.lastCall![0]).conditions).toEqual(raw);
    expect(screen.queryByLabelText(t("jsonLabel"))).toBeNull();
  });

  it("translates each known client validation message while preserving unchanged pure validator rules", () => {
    for (const [condition, key] of errors) {
      expect(conditionError(condition)).not.toBeNull();
      expect(localizedConditionError(condition, (k) => t(k))).toBe(t(key));
    }
    expect(localizedConditionError(policy.conditions[0], (k) => t(k))).toBeNull();
    expect(localizedConditionError({ kind: "custom", raw: rawCondition }, (k) => t(k))).toBeNull();
    render(
      provider(
        <PolicySentence
          policy={{ ...policy, conditions: [errors[0][0], errors[5][0]] }}
          onChange={() => {}}
        />
      )
    );
    expect(screen.getByText(t("error.days"))).toBeInTheDocument();
    expect(screen.getByText(t("error.cidrFormat"))).toBeInTheDocument();
  });

  it("preserves ICU argument/rich-tag contracts for every reordered sentence", () => {
    const actual = leaves(messages.shared.access.policy);
    const english = leaves(en.shared.access.policy);
    expect(Object.keys(actual)).toEqual(Object.keys(english));
    for (const key of Object.keys(english))
      expect(contract(parse(actual[key]))).toEqual(contract(parse(english[key])));
  });
});

it("retains opaque native JSON parser diagnostics and draft text without dispatching a malformed change", () => {
  const change = vi.fn();
  render(
    <NextIntlClientProvider locale="ja" messages={ja}>
      <Editor onChange={change} />
    </NextIntlClientProvider>
  );
  fireEvent.click(screen.getByRole("button", { name: ja.shared.access.policy.jsonEdit }));
  const field = screen.getByLabelText(ja.shared.access.policy.jsonLabel);
  const invalid = '[{"opaque":';
  let message = "";
  try {
    JSON.parse(invalid);
  } catch (err) {
    message = (err as Error).message;
  }
  fireEvent.change(field, { target: { value: invalid } });
  fireEvent.click(screen.getByRole("button", { name: ja.shared.access.policy.apply }));
  expect(screen.getByText(message)).toBeInTheDocument();
  expect(field).toHaveValue(invalid);
  expect(change).not.toHaveBeenCalled();
});
