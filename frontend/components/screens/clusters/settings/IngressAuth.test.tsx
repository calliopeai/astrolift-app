import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NextIntlClientProvider } from "next-intl";
import messages from "@/messages/en.json";

import { INGRESS_AUTH } from "./fixtures";
import { IngressAuthView } from "./IngressAuth";

describe("ingress Apply progress", () => {
  it("shows progress during a save before reconciliation starts", () => {
    render(
      <NextIntlClientProvider locale="en" messages={messages}>
        <IngressAuthView {...INGRESS_AUTH} busy reconciling={false} />
      </NextIntlClientProvider>
    );
    const apply = screen.getByRole("button", { name: messages.clusterSettings.ingressAuth.apply });
    expect(apply).toBeDisabled();
    expect(apply.querySelector(".animate-spin")).not.toBeNull();
  });
});
