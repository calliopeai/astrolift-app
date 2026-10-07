import { runStory } from "@/test/run-story";
import { cleanup } from "@testing-library/react";
import { composeStories, setProjectAnnotations } from "@storybook/react";
import { describe, expect, it } from "vitest";
import * as preview from "../../../.storybook/preview";
import * as handshake from "../apps/domains/DomainHandshakeCard.stories";
import * as email from "../apps/managed-services/EmailDetailSheet.stories";
import * as detail from "./ManagedDomainDetail.stories";
import * as list from "./ManagedDomainsScreen.stories";

// calliope-installer#447: with DNS withheld each surface gives the reason.
// Every unset story renders the same fixture and interaction as its withheld
// pair with only dnsRestriction removed, and its play asserts the note absent.
setProjectAnnotations(preview);
const { AddZoneDnsWithheld, AddZoneDnsUnset } = composeStories(list);
const { DnsWithheld, DnsUnset } = composeStories(detail);
const { PlatformZoneDnsWithheld, PlatformZoneDnsUnset } = composeStories(handshake);
const { IdentityPendingDnsWithheld, IdentityPendingDnsUnset } = composeStories(email);

describe("DNS withheld notes", () => {
  it.each([
    ["add zone, withheld", AddZoneDnsWithheld, true],
    ["add zone, unset", AddZoneDnsUnset, false],
    ["zone detail, withheld", DnsWithheld, true],
    ["zone detail, unset", DnsUnset, false],
    ["platform zone domain, withheld", PlatformZoneDnsWithheld, true],
    ["platform zone domain, unset", PlatformZoneDnsUnset, false],
    ["SES identity, withheld", IdentityPendingDnsWithheld, true],
    ["SES identity, unset", IdentityPendingDnsUnset, false],
  ] as const)("%s", async (_name, Story, shown) => {
    const canvasElement = document.createElement("div");
    document.body.append(canvasElement);
    try {
      await runStory(Story, canvasElement);
      expect(document.body.textContent?.includes("DNS is withheld")).toBe(shown);
    } finally {
      cleanup();
      canvasElement.remove();
    }
  });
});
