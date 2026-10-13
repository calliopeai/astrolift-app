import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ja from "@/messages/ja.json";

import { DetailStatusBadge, DetailTimestamp } from "./EntityDetailShell";

const NOW = new Date("2026-10-12T12:00:00Z");

function inJapanese(ui: ReactNode) {
  return render(
    <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
      {ui}
    </NextIntlClientProvider>
  );
}

describe("detail header copy in the viewer's language (#2381)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it("names a status the catalog knows, and title-cases one it does not", () => {
    inJapanese(
      <>
        <DetailStatusBadge status="timed_out" />
        <DetailStatusBadge status="FAILED" />
        <DetailStatusBadge status="future_state" />
      </>
    );
    expect(screen.getByText(ja.detailShell.statuses.timed_out)).toBeInTheDocument();
    expect(screen.getByText(ja.detailShell.statuses.failed)).toBeInTheDocument();
    expect(screen.getByText("Future State")).toBeInTheDocument();
  });

  it("keeps a caller's own label", () => {
    inJapanese(<DetailStatusBadge status="running" label="LABEL_LITERAL" />);
    expect(screen.getByText("LABEL_LITERAL")).toBeInTheDocument();
  });

  it("gives a relative age, then the date past 30 days", () => {
    const minutesAgo = new Date(NOW.getTime() - 5 * 60_000).toISOString();
    const longAgo = "2026-06-01T00:00:00Z";
    inJapanese(
      <>
        <DetailTimestamp iso={minutesAgo} />
        <DetailTimestamp iso={longAgo} />
      </>
    );
    expect(screen.getByText(ja.relativeAge.minutes.replace("{count}", "5"))).toBeInTheDocument();
    expect(
      screen.getByText(
        new Intl.DateTimeFormat("ja", {
          month: "short",
          day: "numeric",
          year: "numeric",
          timeZone: "UTC",
        }).format(new Date(longAgo))
      )
    ).toBeInTheDocument();
  });
});
