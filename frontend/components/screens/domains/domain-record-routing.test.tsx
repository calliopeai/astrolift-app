import { parse, TYPE } from "@formatjs/icu-messageformat-parser";
import { render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
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
import { DomainRecordRouting, type DomainRecordRoutingData } from "./DomainRecordRouting";
import { ROUTING_EXAMPLES } from "./DomainRecordRouting.stories";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
function show(routing: DomainRecordRoutingData | null, locale: keyof typeof catalogs = "en") {
  return render(
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      timeZone="UTC"
      onError={(error) => {
        throw error;
      }}
    >
      <DomainRecordRouting routing={routing} />
    </NextIntlClientProvider>
  );
}

describe("record routing metadata", () => {
  it("omits absent Route53 metadata without presenting another provider as simple", () => {
    expect(show(null).container).toBeEmptyDOMElement();
  });
  it("retains zero and false, and separates references from health observations", () => {
    const { container } = show({
      ...ROUTING_EXAMPLES[1],
      multiValueAnswer: false,
      healthCheckId: "health-ref",
      trafficPolicyInstanceId: "traffic-ref",
    });
    expect(container.querySelector("details")?.open).toBe(false);
    expect(screen.getByText("0")).toHaveRole("definition");
    expect(screen.getByText("No")).toHaveRole("definition");
    expect(screen.getByText("health-ref")).toHaveRole("definition");
    expect(screen.getByText("traffic-ref")).toHaveRole("definition");
    expect(screen.getByText(en.domainRecordRouting.help)).toBeInTheDocument();
    expect(container.querySelector("button,a")).toBeNull();
  });
  it("shows unavailable classification without displaying private raw reasons or future policy keys", () => {
    show({
      policy: "FUTURE_PRIVATE_VALUE" as DomainRecordRoutingData["policy"],
      reason: "PRIVATE_PROVIDER_REPLY",
    });
    expect(screen.getByText(/Unknown/)).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(en.domainRecordRouting.unknownReason);
    expect(screen.queryByText(/PRIVATE/)).toBeNull();
  });
  it("explains conflicting metadata while retaining observed configuration", () => {
    show({
      policy: "UNKNOWN",
      reason: "AMBIGUOUS_ROUTING_POLICY",
      setIdentifier: "observed",
      weight: 5,
      region: "us-west-2",
    });
    expect(screen.getByRole("note")).toHaveTextContent(en.domainRecordRouting.ambiguous);
    expect(screen.getByText("5")).toHaveRole("definition");
    expect(screen.getByText("us-west-2")).toHaveRole("definition");
  });
  it("renders provider identifiers as inert text", () => {
    const value = '<img src=x onerror="private()">';
    const { container } = show({ ...ROUTING_EXAMPLES[1], setIdentifier: value });
    expect(screen.getByText(value)).toHaveRole("definition");
    expect(container.querySelector("img,script,a")).toBeNull();
  });
  it.each(locales)(
    "%s has complete literal copy and renders all policy examples without fallback",
    (locale) => {
      const copy = catalogs[locale].domainRecordRouting;
      expect(Object.keys(copy).sort()).toEqual(Object.keys(en.domainRecordRouting).sort());
      for (const value of Object.values(copy)) {
        expect(value.trim()).not.toBe("");
        expect(parse(value).every((node) => node.type === TYPE.literal)).toBe(true);
      }
      const { container } = render(
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          timeZone="UTC"
          onError={(error) => {
            throw error;
          }}
        >
          {ROUTING_EXAMPLES.map((routing) => (
            <DomainRecordRouting key={routing.policy} routing={routing} />
          ))}
        </NextIntlClientProvider>
      );
      const panels = container.querySelectorAll("details");
      expect(panels).toHaveLength(9);
      expect(within(panels[6] as HTMLElement).getByText("-99")).toHaveRole("definition");
      expect(within(panels[6] as HTMLElement).getByText("180.00")).toHaveRole("definition");
      expect(within(panels[7] as HTMLElement).getByText("*")).toHaveRole("definition");
      expect(within(panels[8] as HTMLElement).getByRole("note")).toHaveTextContent(copy.incomplete);
      expect(container.textContent).not.toContain("domainRecordRouting.");
      if (locale !== "en") expect(copy.help).not.toBe(en.domainRecordRouting.help);
    }
  );
});
