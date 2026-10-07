import { runStory } from "@/test/run-story";
import { cleanup } from "@testing-library/react";
import { composeStories, setProjectAnnotations } from "@storybook/react";
import { describe, expect, it } from "vitest";
import * as preview from "../../../.storybook/preview";
import * as handshake from "../apps/domains/DomainHandshakeCard.stories";
import * as email from "../apps/managed-services/EmailDetailSheet.stories";
import * as detail from "./ManagedDomainDetail.stories";
import * as list from "./ManagedDomainsScreen.stories";

// calliope-installer#447: with DNS withheld every action that would write
// DNS is disabled with the reason, and the plays assert it. Unset shows none.
setProjectAnnotations(preview);
const { AddZoneDnsWithheld, Full: ListFull } = composeStories(list);
const { DnsWithheld, Full: DetailFull } = composeStories(detail);
const { PlatformZoneDnsWithheld, Full: HandshakeFull } = composeStories(handshake);
const { IdentityPendingDnsWithheld, IdentityAndSending } = composeStories(email);

describe("DNS-writing actions with DNS withheld", () => {
  it.each([
    ["add zone, withheld", AddZoneDnsWithheld, true],
    ["zones, unset", ListFull, false],
    ["zone detail, withheld", DnsWithheld, true],
    ["zone detail, unset", DetailFull, false],
    ["platform zone domain, withheld", PlatformZoneDnsWithheld, true],
    ["custom domain, unset", HandshakeFull, false],
    ["SES identity, withheld", IdentityPendingDnsWithheld, true],
    ["SES identity, unset", IdentityAndSending, false],
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
