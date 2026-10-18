import { readFileSync } from "node:fs";
import path from "node:path";
import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import type { ReactNode } from "react";
import { print, buildSchema, validate, execute } from "graphql";
import { fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
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
  TEMPORARY_APP_CAPABILITIES,
  TEMPORARY_APP_POLICY,
  ENVIRONMENT_APP_EXPIRY,
} from "@/graphql/registry/temporary-apps.queries";
import { useTemporaryAppRules } from "./use-temporary-app-rules";

vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "org-guid" } }),
}));

import { TemporaryAppFields } from "./TemporaryAppFields";
import { TEMPORARY_RULES } from "./temporary-app.fixtures";
import {
  durationSeconds,
  manifestExpiry,
  temporaryAppInput,
  temporaryAppValid,
} from "./temporary-app-model";
import { HostBrainAppDialog } from "../brains/HostBrainAppDialog";

const locales = { en, es, fr, de, ja, ko, "pt-BR": pt, "zh-Hans": zh };

it("validates policy/discovery/environment operations against the actual SDL", () => {
  const schema = buildSchema(
    readFileSync(path.resolve(__dirname, "../../../schema.graphql"), "utf8")
  );
  for (const document of [
    TEMPORARY_APP_CAPABILITIES,
    TEMPORARY_APP_POLICY,
    ENVIRONMENT_APP_EXPIRY,
  ]) {
    expect(
      validate(schema, document).map((e) => e.message),
      print(document)
    ).toEqual([]);
  }
});

it("honors manifest inheritance, exact units, policy limits and explicit overrides", () => {
  for (const [value, seconds] of [
    ["30m", 1800],
    ["4h", 14400],
    ["7d", 604800],
    ["6w", 3628800],
    ["3mo", 7776000],
  ] as const)
    expect(durationSeconds(value)).toBe(seconds);
  expect(durationSeconds("1h30m")).toBeNull();
  expect(durationSeconds("0m")).toBeNull();
  expect(manifestExpiry('[app]\nslug="demo"\nexpires_in="6w"\n')).toBe("6w");
  expect(manifestExpiry("[invalid")).toBe("");
  expect(temporaryAppInput({ expiresIn: "4h", deleteDataOnExpiry: true }, "7d")).toEqual({
    expiresIn: "4h",
    expiresAt: null,
    deleteDataOnExpiry: true,
  });
  expect(temporaryAppInput({ deleteDataOnExpiry: true }, "7d")).toEqual({
    expiresIn: null,
    expiresAt: null,
    deleteDataOnExpiry: true,
  });
  expect(temporaryAppInput({ deleteDataOnExpiry: true })).toEqual({
    expiresIn: null,
    expiresAt: null,
    deleteDataOnExpiry: false,
  });
  const required = { ...TEMPORARY_RULES, permanentAllowed: false };
  expect(temporaryAppValid({}, required)).toBe(false);
  expect(temporaryAppValid({}, required, "7d")).toBe(true);
  expect(temporaryAppValid({ expiresIn: "1m" }, required)).toBe(false);
  expect(temporaryAppValid({ expiresIn: "91d" }, required)).toBe(false);
  expect(temporaryAppValid({ expiresIn: "7d" }, null)).toBe(false);
  expect(temporaryAppValid({}, TEMPORARY_RULES)).toBe(true);
});

describe("all eight catalogs render current limits and irreversible data choices", () => {
  for (const [locale, messages] of Object.entries(locales))
    it(locale, () => {
      const errors = vi.fn();
      const onChange = vi.fn();
      const view = render(
        <NextIntlClientProvider locale={locale} messages={messages} onError={errors}>
          <TemporaryAppFields
            value={{ expiresIn: "7d" }}
            onChange={onChange}
            rules={TEMPORARY_RULES}
          />
        </NextIntlClientProvider>
      );
      expect(
        screen.getByLabelText(messages.apps.temporaryCreation.ttl).getAttribute("aria-invalid")
      ).toBe("false");
      fireEvent.click(screen.getByRole("checkbox"));
      expect(onChange).toHaveBeenCalledWith({ expiresIn: "7d", deleteDataOnExpiry: true });
      expect(errors).not.toHaveBeenCalled();
      view.unmount();
    });
});

it("requires a lifetime when policy requires it and blocks host submission while policy is unavailable", () => {
  const onSubmit = vi.fn();
  const props = {
    brainSlug: "demo-brain",
    projects: [{ id: "p1", label: "platform/demo" }],
    submitting: false,
    onSubmit,
    onDismiss: vi.fn(),
  };
  const temporary = {
    rules: { ...TEMPORARY_RULES, permanentAllowed: false },
    loading: false,
    failed: false,
  };
  const view = render(
    <NextIntlClientProvider locale="en" messages={en}>
      <HostBrainAppDialog {...props} temporary={temporary} />
    </NextIntlClientProvider>
  );
  fireEvent.change(screen.getByLabelText("Image"), { target: { value: "ghcr.io/example/demo:1" } });
  expect(screen.getByRole("button", { name: "Create app" }).hasAttribute("disabled")).toBe(true);
  fireEvent.change(screen.getByLabelText("App lifetime"), { target: { value: "4h" } });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Create app" }));
  expect(onSubmit).toHaveBeenCalledWith({
    projectId: "p1",
    image: "ghcr.io/example/demo:1",
    name: "",
    port: 8080,
    expiresIn: "4h",
    deleteDataOnExpiry: true,
  });
  view.rerender(
    <NextIntlClientProvider locale="en" messages={en}>
      <HostBrainAppDialog {...props} temporary={{ rules: null, loading: false, failed: true }} />
    </NextIntlClientProvider>
  );
  expect(screen.getByRole("button", { name: "Create app" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("alert").textContent).toContain("could not be read");
});

it("reads the actual policy through Apollo and honors member-only permanence without an admin grant", async () => {
  const schema = buildSchema(
    readFileSync(path.resolve(__dirname, "../../../schema.graphql"), "utf8")
  );
  const fields: string[] = [];
  const link = new ApolloLink(
    (operation) =>
      new Observable((observer) => {
        fields.push(operation.operationName ?? "");
        const result = execute({
          schema,
          document: operation.query,
          variableValues: operation.variables,
          rootValue: {
            astroliftServerInfo: { capabilities: ["apps.temporary_expiry"] },
            astroliftTemporaryAppPolicy: {
              ...TEMPORARY_RULES.policy,
              maxTtlSeconds: 7200,
              dataGraceDays: 3,
              temporaryOnlyForMembers: true,
            },
          },
        });
        Promise.resolve(result).then((data) => {
          observer.next(data);
          observer.complete();
        });
      })
  );
  const client = new ApolloClient({ cache: new InMemoryCache(), link });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ApolloProvider client={client}>
      <PermissionsProvider value={{ granted: new Set(), loading: false }}>
        {children}
      </PermissionsProvider>
    </ApolloProvider>
  );
  const { result, unmount } = renderHook(() => useTemporaryAppRules(), { wrapper });
  await waitFor(() => expect(result.current.rules?.policy.maxTtlSeconds).toBe(7200));
  expect(result.current.rules?.policy.dataGraceDays).toBe(3);
  expect(result.current.rules?.permanentAllowed).toBe(false);
  expect(result.current.loading).toBe(false);
  expect(result.current.failed).toBe(false);
  expect(fields).toEqual(["TemporaryAppCapabilities", "TemporaryAppPolicy"]);
  unmount();
  client.stop();
});
