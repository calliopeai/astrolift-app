import { parse, TYPE } from "@formatjs/icu-messageformat-parser";
import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider, createTranslator } from "next-intl";
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
import { DnsConnectionWizard } from "./DnsConnectionWizard";
import { DnsConnectionCallbackNotice } from "./DnsConnectionCallbackNotice";
import { DNS_SETUP } from "./DnsConnectionWizard.stories";
const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
const keys = [
  "callbackSaved",
  "callbackUnconfirmed",
  "callbackDenied",

  "title",
  "description",
  "providerStep",
  "connectionStep",
  "zoneStep",
  "reviewStep",
  "verifyStep",
  "cloudflareHelp",
  "route53Help",
  "readOnly",
  "connectionName",
  "token",
  "tokenHelp",
  "saveToken",
  "oauth",
  "oauthUnavailable",
  "connections",
  "connectionsEmpty",
  "chooseConnection",
  "retest",
  "disconnect",
  "disconnectTitle",
  "disconnectHelp",
  "previousPage",
  "nextPage",
  "selectZone",
  "zoneEmpty",
  "inventoryIncomplete",
  "noWrites",
  "attachTarget",
  "delegationHelp",
  "proxied",
  "acknowledge",
  "register",
  "attach",
  "back",
  "verificationHelp",
  "verify",
  "openDomain",
  "route53Empty",
  "configureRoute53",
  "unavailable",
  "targetUnavailable",
  "uncertain",
  "refused",
  "acceptedUnverified",
  "refreshFailed",
  "connectionSaved",
  "retestAccepted",
  "disconnectAccepted",
  "registrationAccepted",
  "ownershipVerified",
  "verifyPending",
] as const;
describe("all-eight DNS connection copy", () => {
  it.each(locales)("%s preserves exact new keys and zero ICU arguments", (locale) => {
    const copy = catalogs[locale].domainConnections;
    expect(Object.keys(copy).sort()).toEqual([...keys].sort());
    for (const key of keys) {
      expect(copy[key].trim()).not.toBe("");
      if (locale !== "en") expect(copy[key]).not.toBe(en.domainConnections[key]);
      expect(parse(copy[key]).every((node) => node.type === TYPE.literal)).toBe(true);
      const t = createTranslator({
        locale,
        messages: copy,
        onError: (error) => {
          throw error;
        },
      });
      expect(t(key)).not.toContain("domainConnections.");
    }
  });
  it.each(locales)(
    "%s renders the read-only review and acknowledgement without fallback",
    (locale) => {
      const errors: Error[] = [];
      render(
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          timeZone="UTC"
          onError={(error) => errors.push(error)}
        >
          <DnsConnectionWizard {...DNS_SETUP} provider="cloudflare" initialStep="review" />
          <DnsConnectionCallbackNotice outcome="saved" />
        </NextIntlClientProvider>
      );
      const copy = catalogs[locale].domainConnections;
      expect(screen.getByText(copy.callbackSaved)).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: copy.title })).toBeInTheDocument();
      expect(screen.getByLabelText(copy.acknowledge)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: copy.register })).toBeDisabled();
      expect(screen.getByText(copy.noWrites)).toBeInTheDocument();
      expect(errors).toEqual([]);
    }
  );
});

it.each(locales)("%s explains platform operator refusal instead of a raw code", (locale) => {
  const errors: Error[] = [];
  render(
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      timeZone="UTC"
      onError={(e) => errors.push(e)}
    >
      <DnsConnectionWizard
        {...DNS_SETUP}
        allowed={false}
        support={{ ...DNS_SETUP.support!, allowed: false, reason: "PLATFORM_OPERATOR_REQUIRED" }}
      />
    </NextIntlClientProvider>
  );
  expect(
    screen.getByText(catalogs[locale].managedDomains.platformOperatorRequired)
  ).toBeInTheDocument();
  expect(screen.queryByText("PLATFORM_OPERATOR_REQUIRED")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Cloudflare" })).toBeDisabled();
  expect(errors).toEqual([]);
});
