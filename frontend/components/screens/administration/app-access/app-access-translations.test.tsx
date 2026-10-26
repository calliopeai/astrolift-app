import { render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";

import { useLocalListState } from "@/components/list/use-list-state";
import { useLocalizedList } from "@/components/list/use-localized-list";
import de from "@/messages/de.json";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";

import {
  AppAccessOverviewScreen,
  type AppAccessOverviewScreenProps,
} from "./AppAccessOverviewScreen";
import { EDGE_ACCESS_LIST, EDGE_STATUSES } from "./edge-access-list";
import { APP_ACCESS, PERSON, PERSON_ROWS } from "./fixtures";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };

function Overview(props: Partial<Omit<AppAccessOverviewScreenProps, "list">>) {
  const list = useLocalListState(useLocalizedList(EDGE_ACCESS_LIST));
  return <AppAccessOverviewScreen {...APP_ACCESS} list={list} {...props} />;
}

function inLocale(locale: keyof typeof catalogs, ui: React.ReactElement) {
  return render(
    <NextIntlClientProvider locale={locale} messages={catalogs[locale]} timeZone="UTC">
      {ui}
    </NextIntlClientProvider>
  );
}

describe("Admin › App access (#2534)", () => {
  it("shows every status with what it means, and links each row to the app's editor", () => {
    inLocale("en", <Overview />);
    const table = screen.getByRole("table");
    for (const label of ["Enforced", "Applying", "Next deploy", "Drift", "Unverified", "No edge"]) {
      expect(within(table).getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(within(table).getByText("Takes effect on the next deploy")).toBeInTheDocument();
    expect(within(table).getByText("astrolift.toml")).toBeInTheDocument();
    expect(within(table).getAllByRole("link", { name: /conflict-bdr/ })[0]).toHaveAttribute(
      "href",
      "/apps/conflict-bdr/access?section=edge"
    );
    // Without a person there is no "gets in through" column.
    expect(within(table).queryByText("Gets in through")).toBeNull();
  });

  it("for one person, adds how they get in and the logins behind it", () => {
    inLocale("en", <Overview rows={PERSON_ROWS} person={PERSON} />);
    expect(screen.getByText("What eric@calliope.ai may enter")).toBeInTheDocument();
    expect(screen.getByText(/4 other environments refuse them/)).toBeInTheDocument();
    expect(screen.getByText("Login found; groups: conflict-sales, bdr")).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getByText("Gets in through")).toBeInTheDocument();
    expect(within(table).getByText("Group: conflict-sales")).toBeInTheDocument();
    expect(within(table).getByText("Listed by email")).toBeInTheDocument();
  });

  it.each(Object.keys(catalogs).filter((l) => l !== "en") as (keyof typeof catalogs)[])(
    "renders in %s without an English status left over",
    (locale) => {
      inLocale(locale, <Overview />);
      const labels = (catalogs[locale] as typeof en).adminAppAccess.status.labels;
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
        (catalogs[locale] as typeof en).adminAppAccess.title
      );
      for (const status of EDGE_STATUSES.filter((s) => s !== "open")) {
        expect(screen.getAllByText(labels[status]).length).toBeGreaterThan(0);
      }
    }
  );
});
