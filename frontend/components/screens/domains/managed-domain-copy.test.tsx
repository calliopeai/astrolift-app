import { parse, TYPE } from "@formatjs/icu-messageformat-parser";
import { render, screen } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";
import { locales } from "@/i18n/config";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";
import { ManagedDomainDetail } from "./ManagedDomainDetail";
import { domainDiagnosticReasonKey } from "./domain-diagnostic-reasons";
import {
  DNS_ANSWER_OBSERVATION,
  DNS_NO_DATA,
  ICMP_TIMEOUT,
  OPERATOR_REQUIRED,
} from "./ManagedDomainDetail.stories";
import { MANAGED_DOMAIN } from "./domains-environments.fixtures";
const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
const keys = [
  "platformOperatorRequired",
  "dnsNoData",
  "icmpTimeout",
  "dnsNoDataStatus",
  "answerObserved",
  "dnsAnswerHelp",
  "delegationMatch",
  "delegationScopeHelp",
  "internalDnsUnavailable",
  "mine",
  "status",
  "listSearch",
  "mineNote",
  "listBounded",
  "cloudflareReadOnly",

  "cloudflareBinding",
  "priority",

  "zoneRoot",
  "selectDriver",
  "none",
  "tenantApps",
  "previewEnvironments",
  "both",
  "wildcardHelp",
  "cancel",

  "title",
  "overview",
  "records",
  "routing",
  "diagnostics",
  "loading",
  "notFound",
  "readFailed",
  "refresh",
  "provisioned",
  "unprovisioned",
  "provisioning",
  "provisionHelp",
  "zone",
  "driver",
  "defaultFor",
  "wildcard",
  "yes",
  "no",
  "organization",
  "created",
  "verification",
  "challenge",
  "challengeHelp",
  "providerZone",
  "publicDelegation",
  "expected",
  "observed",
  "mismatchHelp",
  "unknownHelp",
  "perspective",
  "checkedAt",
  "reason",
  "copy",
  "copied",
  "copyFailed",
  "recordSearch",
  "recordType",
  "all",
  "name",
  "ttl",
  "value",
  "alias",
  "noRecords",
  "recordsTruncated",
  "unknown",
  "ok",
  "mismatch",
  "unavailable",
  "unsupported",
  "application",
  "environment",
  "cluster",
  "ingress",
  "routingTarget",
  "tls",
  "routeHelp",
  "noRoutes",
  "routesTruncated",
  "hostname",
  "tool",
  "run",
  "running",
  "probeHelp",
  "lookup",
  "dig",
  "ping",
  "traceroute",
  "https",
  "latency",
  "response",
  "emptyProbe",
  "refreshCheck",
  "versionChanged",
  "back",
  "configuration",
  "address",
  "httpStatus",
  "tlsVerified",
  "recordCount",
  "challengeName",
  "challengeValue",
  "internalDns",
  "effectiveTenant",
  "effectivePreview",
  "revalidate",
  "delete",
  "deleteTitle",
  "deleteHelp",
  "actionFailed",
  "actionUncertain",
  "acceptedUnverified",
  "revalidationAccepted",
  "refreshFailed",
  "removed",
  "zoneId",
  "visibility",
  "privateZone",
  "publicZone",
  "bindingSource",
  "platformBinding",
  "operatorBinding",
  "listHelp",
  "add",
  "listEmpty",
  "listEmptyHelp",
  "nameservers",
  "open",
  "adding",
  "createTitle",
  "createHelp",
  "createAccepted",
  "copyNameservers",
  "defaultHelp",
  "typeFilter",
  "aliasHealth",
] as const;
// These international protocol/tool words are also valid localized labels.
const internationalWords: Partial<Record<(typeof locales)[number], readonly string[]>> = {
  es: ["no", "ping"],
  fr: ["zone", "ping"],
  de: ["routing", "zone", "name", "hostname", "ping"],
  "pt-BR": ["ping"],
};
describe("managed domain multilingual contract", () => {
  it.each(locales)("%s has exact keys and ICU arguments without English fallback", (locale) => {
    const copy = catalogs[locale].managedDomains;
    expect(Object.keys(copy).sort()).toEqual([...keys].sort());
    for (const key of keys) {
      expect(copy[key].trim()).not.toBe("");
      if (locale !== "en" && !internationalWords[locale]?.includes(key))
        expect(copy[key], `${locale}.${key}`).not.toBe(en.managedDomains[key]);
      const args = parse(copy[key]).flatMap((node) => {
        if (node.type === TYPE.literal) return [];
        expect(node.type, `${locale}.${key}`).toBe(TYPE.argument);
        return "value" in node ? [node.value] : [];
      });
      expect(args, `${locale}.${key}`).toEqual(key === "recordCount" ? ["count"] : []);
      const t = createTranslator({
        locale,
        messages: copy,
        onError: (error) => {
          throw error;
        },
      });
      expect(t(key, { count: 3 })).not.toContain("{count}");
    }
  });
  it.each(locales)(
    "%s renders actual localized observations and confirmation controls",
    (locale) => {
      const errors: Error[] = [];
      render(
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          timeZone="UTC"
          onError={(error) => errors.push(error)}
        >
          <ManagedDomainDetail {...MANAGED_DOMAIN} />
        </NextIntlClientProvider>
      );
      const copy = catalogs[locale].managedDomains;
      expect(screen.getByRole("tab", { name: copy.overview })).toBeInTheDocument();
      expect(screen.getByText(copy.provisioned)).toBeInTheDocument();
      expect(screen.getByText(copy.mismatchHelp)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: copy.revalidate })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: copy.delete })).toBeInTheDocument();
      expect(errors).toEqual([]);
    }
  );
});

it.each(locales)(
  "%s preserves technical evidence and uncertainty with useful explanations",
  (locale) => {
    const errors: Error[] = [];
    for (const [args, key, code] of [
      [OPERATOR_REQUIRED, "platformOperatorRequired", "PLATFORM_OPERATOR_REQUIRED"],
      [DNS_NO_DATA, "dnsNoData", "DNS_NO_DATA"],
      [ICMP_TIMEOUT, "icmpTimeout", "ICMP_TIMEOUT"],
    ] as const) {
      const view = render(
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          timeZone="UTC"
          onError={(e) => errors.push(e)}
        >
          <ManagedDomainDetail {...args} />
        </NextIntlClientProvider>
      );
      expect(screen.getByText(catalogs[locale].managedDomains[key])).toBeInTheDocument();
      expect(screen.getByText(code)).toBeInTheDocument();
      const expectedState =
        code === "PLATFORM_OPERATOR_REQUIRED"
          ? "unsupported"
          : code === "DNS_NO_DATA"
            ? "dnsNoDataStatus"
            : "unknown";
      expect(
        screen.getAllByText(catalogs[locale].managedDomains[expectedState]).length
      ).toBeGreaterThan(0);
      view.unmount();
    }
    expect(errors).toEqual([]);
  }
);
it("unknown evidence reasons cannot become a translated success", () => {
  expect(domainDiagnosticReasonKey("DNS_NO_DATA")).toBe("dnsNoData");
  expect(domainDiagnosticReasonKey("constructor")).toBeNull();
  expect(domainDiagnosticReasonKey("DNS_ANSWER_OBSERVED")).toBeNull();
  expect(domainDiagnosticReasonKey("DNS_ANSWER")).toBe("dnsAnswerHelp");
});

it.each(locales)(
  "%s renders answer observation separately from NS match and internal DNS",
  (locale) => {
    const errors: string[] = [];
    const view = render(
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        timeZone="UTC"
        onError={(error) => errors.push(error.message)}
      >
        <ManagedDomainDetail {...DNS_ANSWER_OBSERVATION} />
      </NextIntlClientProvider>
    );
    const copy = catalogs[locale].managedDomains;
    expect(screen.getAllByText(copy.answerObserved)).toHaveLength(2);
    expect(screen.getAllByText(copy.dnsAnswerHelp)).toHaveLength(2);
    expect(screen.getAllByText(copy.delegationMatch)).toHaveLength(2);
    expect(screen.getAllByText(copy.delegationScopeHelp)).toHaveLength(2);
    expect(screen.getByText(copy.internalDnsUnavailable)).toBeInTheDocument();
    expect(screen.getAllByText("DNS_ANSWER")).toHaveLength(2);
    expect(screen.getByText("INTERNAL_DNS_PROBE_NOT_CONFIGURED")).toBeInTheDocument();
    expect(errors).toEqual([]);
    view.unmount();
  }
);
