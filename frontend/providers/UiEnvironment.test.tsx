import { useUI } from "@calliopelabs/ui";
import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";

import fr from "@/messages/fr.json";

import { UiEnvironment } from "./UiEnvironment";

function Probe() {
  const { Link, messages } = useUI();
  return (
    <div>
      <p>{messages.confirm.cancel}</p>
      <p>{messages.table.results(2)}</p>
      <p>{messages.breadcrumb.switchFrom("Clusters")}</p>
      <p>{messages.dialog.close}</p>
      <p>{messages.states.learnMore}</p>
      <p>{messages.states.errorId("abc123")}</p>
      <Link href="/clusters">clusters</Link>
    </div>
  );
}

describe("UiEnvironment", () => {
  it("hands @calliopelabs/ui the app's translations and next/link", () => {
    render(
      <NextIntlClientProvider locale="fr" messages={fr} timeZone="UTC">
        <UiEnvironment>
          <Probe />
        </UiEnvironment>
      </NextIntlClientProvider>
    );

    expect(screen.getByText(fr.shared.confirmation.cancel)).toBeInTheDocument();
    expect(screen.getByText("2 résultats")).toBeInTheDocument();
    expect(screen.getByText("Changer : Clusters")).toBeInTheDocument();
    expect(screen.getByText(fr.uiPrimitives.close)).toBeInTheDocument();
    expect(screen.getByText(fr.shared.list.learnMore)).toBeInTheDocument();
    expect(
      screen.getByText(fr.errorScreens.errorId.replace("{digest}", "abc123"))
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "clusters" })).toHaveAttribute("href", "/clusters");
  });
});
