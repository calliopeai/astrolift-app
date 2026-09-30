import { readFileSync } from "node:fs";
import path from "node:path";
import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { createTranslator, NextIntlClientProvider, useTranslations } from "next-intl";
import * as React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useLocalListState } from "@/components/list/use-list-state";
import { locales } from "@/i18n/config";
import { AgentSecretBundlesView } from "../list/AgentSecretBundles";
import { AgentSecretsView } from "../list/AgentSecrets";
import {
  BUNDLES,
  BUNDLES_LIST,
  SECRET_ROWS,
  SECRETS,
} from "../list/agents-dispatch-secrets.fixtures";
import { useAgentSecretBundles } from "../list/use-agent-secret-bundles";
import { useAgentSecrets } from "../list/use-agent-secrets";
import { AgentSecretValues, type AgentSecretValuesProps } from "./AgentSecretsTab";
import {
  AGENT_SECRETS_LIST,
  agentSecretsPageVariables,
  localizedAgentSecretsList,
} from "./agent-secrets-list";

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "actual-org" } }),
}));
beforeEach(() => {
  toast.success.mockClear();
  toast.error.mockClear();
  localStorage.clear();
});
const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8")),
  ])
);
function leaves(data: Record<string, unknown>, prefix = ""): Record<string, string> {
  return Object.fromEntries(
    Object.entries(data).flatMap(([key, value]) => {
      const name = prefix ? `${prefix}.${key}` : key;
      return typeof value === "string"
        ? [[name, value]]
        : Object.entries(leaves(value as Record<string, unknown>, name));
    })
  );
}
function shape(elements: MessageFormatElement[]): string[] {
  return [
    ...new Set(
      elements.flatMap((node): string[] => {
        if (node.type === 0 || node.type === 7) return [];
        if (node.type === 8) return [`tag:${node.value}`, ...shape(node.children)];
        if (node.type === 5 || node.type === 6)
          return [
            `${node.type}:${node.value}`,
            ...Object.values(node.options).flatMap((option) => shape(option.value)),
          ];
        return [`${node.type}:${node.value}`];
      })
    ),
  ].sort();
}
function provider(locale: string, children: React.ReactNode, onError = vi.fn()) {
  return render(
    <NextIntlClientProvider
      locale={locale}
      timeZone="UTC"
      messages={catalogs[locale]}
      onError={onError}
    >
      {children}
    </NextIntlClientProvider>
  );
}
function Values({ overrides = {} }: { overrides?: Partial<AgentSecretValuesProps> }) {
  const t = useTranslations("agentSecrets.values");
  const definition = React.useMemo(() => localizedAgentSecretsList(t), [t]);
  const list = useLocalListState(definition);
  return (
    <AgentSecretValues
      {...SECRETS}
      list={list}
      rows={[SECRET_ROWS[0]]}
      loading={false}
      stale={false}
      error={null}
      totalCount={1}
      readError="ORIGINAL_STORE_DIAGNOSTIC"
      {...overrides}
    />
  );
}

describe("selected recipe secret translations", () => {
  it.each(locales)(
    "%s has complete ICU contracts and retains static/parser/backend IDs",
    (locale) => {
      const messages = catalogs[locale];
      for (const domain of ["values", "bundles", "feedback"]) {
        const translated = leaves(messages.agentSecrets[domain]);
        const canonical = leaves(catalogs.en.agentSecrets[domain]);
        expect(Object.keys(translated).sort()).toEqual(Object.keys(canonical).sort());
        for (const [key, message] of Object.entries(translated))
          expect(shape(parse(message)), `${domain}.${key}`).toEqual(shape(parse(canonical[key])));
      }
      const t = createTranslator({ locale, messages, namespace: "agentSecrets.values" });
      const definition = localizedAgentSecretsList(t);
      expect(definition.id).toBe(AGENT_SECRETS_LIST.id);
      expect(
        definition.fields.map((field) => [field.key, field.options?.map((option) => option.value)])
      ).toEqual(
        AGENT_SECRETS_LIST.fields.map((field) => [
          field.key,
          field.options?.map((option) => option.value),
        ])
      );
      expect(definition.defaultSort).toEqual(AGENT_SECRETS_LIST.defaultSort);
      expect(definition.fields[0].options?.[0].label).toBe(t("state.set"));
      expect(
        agentSecretsPageVariables({
          filters: { status: "error", provider: "vault" },
          q: "TOKEN",
          sort: [{ key: "envVar", dir: "desc" }],
          page: 3,
          pageSize: 50,
        })
      ).toEqual({
        search: "TOKEN",
        filter: { failing: true, provider: ["vault"] },
        sort: "-envVar",
        page: 3,
        pageSize: 50,
      });
    }
  );
  it.each(locales)(
    "%s keeps value/binding targets, failure drafts and original diagnostics",
    async (locale) => {
      const t = createTranslator({
        locale,
        messages: catalogs[locale],
        namespace: "agentSecrets.values",
      });
      const save = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
      const remove = vi.fn().mockResolvedValue(undefined);
      const onError = vi.fn();
      provider(locale, <Values overrides={{ onSave: save, onRemoveRef: remove }} />, onError);
      expect(screen.getByText(/ORIGINAL_STORE_DIAGNOSTIC/)).toBeInTheDocument();
      const row = screen.getByRole("row", { name: new RegExp(SECRET_ROWS[0].envVar) });
      expect(row).toHaveTextContent(SECRET_ROWS[0].uri);
      fireEvent.pointerDown(within(row).getByRole("button"), { button: 0, ctrlKey: false });
      fireEvent.click(await screen.findByRole("menuitem", { name: t("rotateValue") }));
      const sheet = screen.getByRole("dialog");
      expect(sheet).toHaveTextContent(SECRET_ROWS[0].envVar);
      expect(sheet).toHaveTextContent(SECRET_ROWS[0].uri);
      fireEvent.change(within(sheet).getByLabelText(t("value")), {
        target: { value: "local-regression-value" },
      });
      fireEvent.click(within(sheet).getByRole("button", { name: t("rotate") }));
      await waitFor(() =>
        expect(save).toHaveBeenCalledWith(SECRET_ROWS[0].envVar, "local-regression-value")
      );
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(within(sheet).getByLabelText(t("value"))).toHaveValue("local-regression-value");
      fireEvent.click(within(sheet).getByRole("button", { name: t("rotate") }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      fireEvent.pointerDown(within(row).getByRole("button"), { button: 0, ctrlKey: false });
      fireEvent.click(await screen.findByRole("menuitem", { name: t("removeBinding") }));
      expect(screen.getByRole("alertdialog")).toHaveTextContent(t("removeDescription"));
      fireEvent.click(
        within(screen.getByRole("alertdialog")).getByRole("button", { name: t("removeBinding") })
      );
      await waitFor(() => expect(remove).toHaveBeenCalledWith(SECRET_ROWS[0]));
      expect(onError).not.toHaveBeenCalled();
    }
  );
  it.each(locales)(
    "%s keeps bundle attachment/key actions scoped to the actual row and default recipe",
    async (locale) => {
      const t = createTranslator({
        locale,
        messages: catalogs[locale],
        namespace: "agentSecrets.bundles",
      });
      const attach = vi.fn().mockResolvedValue(false);
      const deleteKey = vi.fn().mockResolvedValue(undefined);
      const onError = vi.fn();
      provider(
        locale,
        <AgentSecretBundlesView
          {...BUNDLES}
          defaultAttachments={[]}
          onAttach={attach}
          onDeleteKey={deleteKey}
        />,
        onError
      );
      const row = screen.getByRole("group", { name: BUNDLES_LIST[0].name });
      fireEvent.change(within(row).getByPlaceholderText(t("prefixPlaceholder")), {
        target: { value: "ACTUAL_" },
      });
      fireEvent.click(within(row).getByRole("button", { name: t("attach") }));
      await waitFor(() =>
        expect(attach).toHaveBeenCalledWith(BUNDLES_LIST[0], "ACTUAL_", 0, undefined)
      );
      expect(within(row).getByPlaceholderText(t("prefixPlaceholder"))).toHaveValue("ACTUAL_");
      const key = BUNDLES_LIST[0].keyNames[0];
      fireEvent.click(within(row).getByRole("button", { name: t("deleteNamed", { name: key }) }));
      const confirm = screen.getByRole("alertdialog");
      expect(confirm).toHaveTextContent(
        t("deleteKeyDescription", { key, name: BUNDLES_LIST[0].name })
      );
      fireEvent.click(within(confirm).getByRole("button", { name: t("delete") }));
      await waitFor(() => expect(deleteKey).toHaveBeenCalledWith(BUNDLES_LIST[0], key));
      expect(onError).not.toHaveBeenCalled();
    }
  );
  it.each(locales)(
    "%s distinguishes refused/unknown reads from empty bindings/bundles and permits actual retry",
    (locale) => {
      const retry = vi.fn();
      const t = createTranslator({
        locale,
        messages: catalogs[locale],
        namespace: "agentSecrets.bundles",
      });
      const shared = createTranslator({
        locale,
        messages: catalogs[locale],
        namespace: "shared.table",
      });
      const a = provider(
        locale,
        <AgentSecretBundlesView
          {...BUNDLES}
          error={{ message: "ORIGINAL_ATTACHMENT_REFUSAL" }}
          onRetry={retry}
        />
      );
      expect(screen.getByRole("alert")).toHaveTextContent("ORIGINAL_ATTACHMENT_REFUSAL");
      expect(screen.queryByText(t("empty"))).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: t("attach") })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: shared("retry") }));
      expect(retry).toHaveBeenCalledOnce();
      a.unmount();
      const b = provider(locale, <AgentSecretBundlesView {...BUNDLES} loading error={null} />);
      expect(screen.getByRole("status")).toHaveTextContent(t("loading"));
      expect(screen.queryByRole("button", { name: t("attach") })).not.toBeInTheDocument();
      b.unmount();
      provider(
        locale,
        <AgentSecretsView
          {...SECRETS}
          embedded
          error={{ message: "ORIGINAL_VALUE_REFUSAL" }}
          onRetry={retry}
        />
      );
      expect(screen.getByRole("alert")).toHaveTextContent("ORIGINAL_VALUE_REFUSAL");
      fireEvent.click(screen.getByRole("button", { name: shared("retry") }));
      expect(retry).toHaveBeenCalledTimes(2);
    }
  );
  it.each(locales)(
    "%s preserves selected-recipe mutation variables, provider diagnostics and default attachment facts",
    async (locale) => {
      const requests: Array<{ operation: string; variables: Record<string, unknown> }> = [];
      const client = new ApolloClient({
        cache: new InMemoryCache(),
        link: new ApolloLink(
          (operation) =>
            new Observable((observer) => {
              requests.push({
                operation: operation.operationName ?? "",
                variables: operation.variables,
              });
              const data =
                operation.operationName === "AgentEnvironmentSpecSecretStatus"
                  ? { agentEnvironmentSpecSecretStatus: SECRET_ROWS }
                  : operation.operationName === "AgentSecretBundles"
                    ? { agentSecretBundles: BUNDLES_LIST }
                    : operation.operationName === "AgentSecretBundleAttachments"
                      ? {
                          agentEnvironmentSpecSecretBundleAttachments: [
                            { ...BUNDLES.defaultAttachments[0], environment: "production" },
                            BUNDLES.defaultAttachments[0],
                          ],
                        }
                      : operation.operationName === "SetAgentSecretValue"
                        ? {
                            setAgentSecretValue: {
                              ok: false,
                              errors: [
                                {
                                  message: "ACTUAL_PROVIDER_DIAGNOSTIC",
                                  code: "REFUSED",
                                  field: null,
                                },
                              ],
                              data: null,
                            },
                          }
                        : operation.operationName === "AttachAgentSecretBundle"
                          ? {
                              attachAgentSecretBundle: {
                                ok: false,
                                errors: [{ message: "ACTUAL_ATTACHMENT_DIAGNOSTIC" }],
                                data: null,
                              },
                            }
                          : {};
              observer.next({ data });
              observer.complete();
            })
        ),
      });
      const onError = vi.fn();
      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <NextIntlClientProvider locale={locale} messages={catalogs[locale]} onError={onError}>
          <ApolloProvider client={client}>{children}</ApolloProvider>
        </NextIntlClientProvider>
      );
      const values = renderHook(() => useAgentSecrets("selected-shared-recipe", true), { wrapper });
      const bundles = renderHook(() => useAgentSecretBundles("selected-shared-recipe", true), {
        wrapper,
      });
      await waitFor(() => expect(values.result.current.rows.length).toBe(SECRET_ROWS.length));
      await waitFor(() => expect(bundles.result.current.defaultAttachments).toHaveLength(1));
      expect(bundles.result.current.defaultAttachments[0].environment).toBe("default");
      let accepted: boolean | undefined;
      await act(async () => {
        accepted = await values.result.current.onSave("ACTUAL_VAR", "actual-local-test-value");
      });
      expect(accepted).toBe(false);
      const t = createTranslator({
        locale,
        messages: catalogs[locale],
        namespace: "agentSecrets.feedback",
      });
      expect(toast.error).toHaveBeenCalledWith(
        t("failure", {
          operation: t("setValueFailed", { name: "ACTUAL_VAR" }),
          message: "ACTUAL_PROVIDER_DIAGNOSTIC",
        })
      );
      await act(async () => {
        accepted = await bundles.result.current.onAttach(BUNDLES_LIST[0], "ACTUAL_", 2);
      });
      expect(accepted).toBe(false);
      expect(toast.error).toHaveBeenCalledWith(
        t("failure", { operation: t("attachFailed"), message: "ACTUAL_ATTACHMENT_DIAGNOSTIC" })
      );
      expect(
        requests.find((request) => request.operation === "SetAgentSecretValue")?.variables
      ).toEqual({
        slug: "selected-shared-recipe",
        envVar: "ACTUAL_VAR",
        value: "actual-local-test-value",
      });
      expect(
        requests.find((request) => request.operation === "AttachAgentSecretBundle")?.variables
      ).toEqual({
        slug: "selected-shared-recipe",
        bundleId: BUNDLES_LIST[0].id,
        prefix: "ACTUAL_",
        position: 2,
      });
      expect(requests.every((request) => request.variables.slug === "selected-shared-recipe")).toBe(
        true
      );
      expect(onError).not.toHaveBeenCalled();
      values.unmount();
      bundles.unmount();
      client.stop();
    }
  );
  it("waits for the actual attachment read, exposes its failure and retries only selected-recipe queries", async () => {
    let attachmentObserver:
      | {
          next: (value: { data: Record<string, unknown> }) => void;
          error: (error: Error) => void;
          complete: () => void;
        }
      | undefined;
    let recovering = false;
    const requests: Array<{ operation: string; slug: unknown }> = [];
    const client = new ApolloClient({
      cache: new InMemoryCache(),
      link: new ApolloLink(
        (operation) =>
          new Observable((observer) => {
            requests.push({
              operation: operation.operationName ?? "",
              slug: operation.variables.slug,
            });
            if (operation.operationName === "AgentSecretBundles") {
              observer.next({ data: { agentSecretBundles: BUNDLES_LIST } });
              observer.complete();
            } else if (operation.operationName === "AgentSecretBundleAttachments") {
              if (recovering) {
                observer.next({ data: { agentEnvironmentSpecSecretBundleAttachments: [] } });
                observer.complete();
              } else attachmentObserver = observer;
            } else observer.error(new Error("Unexpected mutation"));
          })
      ),
    });
    function Wired() {
      return (
        <AgentSecretBundlesView {...useAgentSecretBundles("explicit-selected-recipe", true)} />
      );
    }
    const view = render(
      <NextIntlClientProvider locale="en" timeZone="UTC" messages={catalogs.en}>
        <ApolloProvider client={client}>
          <Wired />
        </ApolloProvider>
      </NextIntlClientProvider>
    );
    await waitFor(() => expect(attachmentObserver).toBeDefined());
    expect(screen.getByRole("status")).toHaveTextContent("Loading bundles");
    expect(screen.queryByRole("button", { name: "Attach" })).not.toBeInTheDocument();
    act(() => attachmentObserver!.error(new Error("ORIGINAL_ATTACHMENT_READ_DENIED")));
    expect(await screen.findByRole("alert")).toHaveTextContent("ORIGINAL_ATTACHMENT_READ_DENIED");
    expect(screen.queryByText("No reusable bundles yet.")).not.toBeInTheDocument();
    recovering = true;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByRole("group", { name: BUNDLES_LIST[0].name });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(requests.every((request) => request.slug === "explicit-selected-recipe")).toBe(true);
    expect(
      requests.every((request) =>
        ["AgentSecretBundles", "AgentSecretBundleAttachments"].includes(request.operation)
      )
    ).toBe(true);
    expect(
      requests.filter((request) => request.operation === "AgentSecretBundleAttachments")
    ).toHaveLength(2);
    view.unmount();
    client.stop();
  });
});
