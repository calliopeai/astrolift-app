import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { buildSchema, execute, parse, validate } from "graphql";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SecretProposalDetailClient } from "@/app/(app)/approvals/secret/[id]/secret-proposal-detail-client";
import { locales } from "@/i18n/config";
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import { DETAIL, PROPOSAL } from "./approvals-b.fixtures";
import {
  SecretProposalDetailScreen,
  type SecretProposalDetailScreenProps,
} from "./SecretProposalDetail";

const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
const operations = [
  ["set", "set"],
  ["delete", "delete"],
  ["attach_bundle", "attachBundle"],
  ["detach_bundle", "detachBundle"],
  ["set_metadata", "setMetadata"],
] as const;
const statuses = ["pending", "approved", "rejected", "applied", "expired", "withdrawn"] as const;
const decisions = ["approved", "rejected"] as const;

function context(locale: string, children: ReactNode) {
  return (
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      timeZone="UTC"
      now={new Date("2026-10-01T00:00:00Z")}
      onError={(error) => {
        throw error;
      }}
    >
      {children}
    </NextIntlClientProvider>
  );
}
function view(locale: string, props: Partial<SecretProposalDetailScreenProps> = {}) {
  return context(locale, <SecretProposalDetailScreen {...DETAIL} {...props} />);
}
function tFor(locale: string) {
  return createTranslator({
    locale,
    messages: catalogs[locale],
    namespace: "approvals.secretProposalDetail",
  });
}
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-01T00:00:00Z"));
});
afterEach(() => vi.useRealTimers());

describe.each(locales)("Secret proposal presentation in %s", (locale) => {
  it("renders the actual client with the unchanged schema-valid literal proposal query", async () => {
    const proposalId = "01990ce1-7c00-7000-8000-000000000001";
    const requests: { operationName: string; variables: unknown }[] = [];
    const fetcher = async (_url: unknown, init?: RequestInit) => {
      const request = JSON.parse(String(init?.body));
      requests.push({ operationName: request.operationName, variables: request.variables });
      const document = parse(request.query);
      expect(validate(schema, document)).toEqual([]);
      const result = await execute({
        schema,
        document,
        variableValues: request.variables,
        rootValue: {
          astroliftSecretChangeProposal: {
            ...PROPOSAL,
            id: proposalId,
            payloadDiff: {},
            status: "pending",
          },
        },
      });
      expect(result.errors).toBeUndefined();
      return new Response(JSON.stringify(result), {
        headers: { "Content-Type": "application/json" },
      });
    };
    const client = new ApolloClient({
      link: new HttpLink({ uri: "http://proposal.invalid/graphql", fetch: fetcher }),
      cache: new InMemoryCache(),
    });
    const t = tFor(locale);
    const result = render(
      context(
        locale,
        <ApolloProvider client={client}>
          <PermissionsProvider value={{ granted: new Set(["secret.approve"]), loading: false }}>
            <SecretProposalDetailClient proposalId={proposalId} />
          </PermissionsProvider>
        </ApolloProvider>
      )
    );
    try {
      expect(
        await screen.findByText(
          t("presentation.summary", { op: t("presentation.operations.set"), env: "prod" })
        )
      ).toBeInTheDocument();
      expect(screen.getByRole("button", { name: t("approve") })).toBeEnabled();
      expect(requests).toEqual([
        { operationName: "GetSecretChangeProposal", variables: { id: proposalId } },
      ]);
    } finally {
      result.unmount();
      client.stop();
    }
  });
  it.each(operations)("localizes operation %s and the exact environment fallback", (op, key) => {
    const t = tFor(locale);
    const operation = t(`presentation.operations.${key}`);
    const summary = t("presentation.summary", { op: operation, env: "LITERAL_ENV" });
    render(
      view(locale, {
        proposal: { ...PROPOSAL, op, environmentName: "LITERAL_ENV", payloadDiff: {} },
      })
    );
    expect(screen.getByText(summary)).toBeInTheDocument();
    expect(screen.getByText(operation)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("LITERAL_ENV");
  });

  it.each(statuses)("localizes %s without changing pending-only actions", (status) => {
    const t = tFor(locale);
    render(view(locale, { proposal: { ...PROPOSAL, status, approvals: [] } }));
    expect(screen.getByText(t(`presentation.statuses.${status}`))).toBeInTheDocument();
    for (const action of ["approve", "reject", "withdraw"]) {
      const button = screen.queryByRole("button", { name: t(action) });
      if (status === "pending") expect(button).toBeEnabled();
      else expect(button).not.toBeInTheDocument();
    }
  });

  it.each(decisions)("localizes approver decision %s and preserves their reason", (decision) => {
    const t = tFor(locale);
    render(
      view(locale, {
        proposal: {
          ...PROPOSAL,
          status: "applied",
          approvals: [{ ...PROPOSAL.approvals[0], decision, reason: "RAW_APPROVER_REASON" }],
        },
      })
    );
    const item = screen.getByRole("listitem");
    expect(within(item).getByText(t(`presentation.decisions.${decision}`))).toBeInTheDocument();
    expect(within(item).getByText("RAW_APPROVER_REASON")).toBeInTheDocument();
  });

  it("uses the localized app-wide fallback without rewriting a supplied summary or diff", () => {
    const t = tFor(locale);
    const proposal = {
      ...PROPOSAL,
      environmentName: "",
      payloadDiff: {
        before: { LITERAL_KEY: "MASKED_BEFORE" },
        after: { LITERAL_KEY: "MASKED_AFTER" },
      },
    };
    const result = render(view(locale, { proposal }));
    expect(
      screen.getByText(
        t("presentation.summary", { op: t("presentation.operations.set"), env: t("appWide") })
      )
    ).toBeInTheDocument();
    expect(screen.getByText("MASKED_BEFORE")).toBeInTheDocument();
    expect(screen.getByText("MASKED_AFTER")).toBeInTheDocument();
    result.rerender(
      view(locale, {
        proposal: {
          ...proposal,
          payloadDiff: { ...proposal.payloadDiff, summary: "RAW_SUPPLIED_SUMMARY" },
          applyError: "RAW_PROVIDER_DIAGNOSTIC",
        },
      })
    );
    expect(screen.getByText("RAW_SUPPLIED_SUMMARY")).toBeInTheDocument();
    expect(
      screen.getByText(t("applyError", { message: "RAW_PROVIDER_DIAGNOSTIC" }))
    ).toBeInTheDocument();
  });

  it.each(["future_value", "constructor", "__proto__"])(
    "keeps unknown technical identity %s literal",
    (value) => {
      const t = tFor(locale);
      render(
        view(locale, {
          proposal: {
            ...PROPOSAL,
            op: value,
            status: value,
            environmentName: "LITERAL_ENV",
            payloadDiff: {},
            approvals: [{ ...PROPOSAL.approvals[0], decision: value }],
          },
        })
      );
      expect(
        screen.getByText(t("presentation.summary", { op: value, env: "LITERAL_ENV" }))
      ).toBeInTheDocument();
      expect(screen.getAllByText(value)).toHaveLength(3);
      expect(screen.queryByRole("button", { name: t("approve") })).not.toBeInTheDocument();
    }
  );

  it("localizes failed-read headings and preserves the diagnostic and retry callback", async () => {
    const t = tFor(locale);
    const onRetry = vi.fn();
    render(
      view(locale, {
        proposal: null,
        error: { name: "Error", message: "RAW_READ_DIAGNOSTIC" },
        onRetry,
      })
    );
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(t("loading.title"));
    expect(screen.getByRole("alert")).toHaveTextContent(t("presentation.loadFailed"));
    expect(screen.getByRole("alert")).toHaveTextContent("RAW_READ_DIAGNOSTIC");
    await userEvent.setup().click(within(screen.getByRole("alert")).getByRole("button"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("changes locale without losing an open proposal confirmation", async () => {
    const t = tFor(locale);
    const nextLocale = locale === "en" ? "es" : "en";
    const result = render(view(locale));
    await userEvent.setup().click(screen.getByRole("button", { name: t("approve") }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent(t("confirmApprove.title"));
    result.rerender(view(nextLocale));
    expect(screen.getByRole("alertdialog")).toHaveTextContent(
      tFor(nextLocale)("confirmApprove.title")
    );
  });

  it("hydrates actual translated markup without a recovery error", async () => {
    const element = view(locale, { proposal: { ...PROPOSAL, payloadDiff: {} } });
    const container = document.createElement("div");
    container.innerHTML = renderToString(element);
    document.body.append(container);
    const onRecoverableError = vi.fn();
    let root: ReturnType<typeof hydrateRoot> | undefined;
    try {
      await act(async () => {
        root = hydrateRoot(container, element, { onRecoverableError });
      });
      expect(onRecoverableError).not.toHaveBeenCalled();
      expect(container).toHaveTextContent(tFor(locale)("presentation.operations.set"));
    } finally {
      await act(async () => root?.unmount());
      container.remove();
    }
  });
});
