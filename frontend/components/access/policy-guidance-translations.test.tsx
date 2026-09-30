import { parse, TYPE, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import de from "@/messages/de.json";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";

import { SIMULATION, SIMULATION_EMPTY } from "./fixtures";
import { PolicyConditionHelp, type ConditionCatalogEntry } from "./PolicyConditionHelp";
import { holderSentence, PolicySimulationPanel } from "./PolicySimulationPanel";

const catalogs = { en, es, fr, de, "pt-BR": pt, ja, ko, "zh-Hans": zh };
const kinds = ["clock", "client_ip", "session", "operation", "__proto__"];
const catalog: ConditionCatalogEntry[] = kinds.map((needs, i) => ({
  kind: `RAW_KIND_${i}`,
  label: `SERVER_LABEL_${i}`,
  description: `SERVER_DESCRIPTION_${i}`,
  needs,
}));

function leaves(obj: Record<string, unknown>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(obj).flatMap(([key, value]) =>
      typeof value === "string"
        ? [[key, value]]
        : Object.entries(leaves(value as Record<string, unknown>)).map(([child, text]) => [
            `${key}.${child}`,
            text,
          ])
    )
  );
}
function contract(nodes: MessageFormatElement[]): string[] {
  return [
    ...new Set(
      nodes.flatMap((node): string[] => {
        if (node.type === TYPE.literal) return [];
        if (node.type === TYPE.tag) return [`tag:${node.value}`, ...contract(node.children)];
        if (node.type === TYPE.plural || node.type === TYPE.select)
          return [`${node.type}:${node.value}`, ...contract(node.options.other.value)];
        return [node.type === TYPE.pound ? "pound" : `${node.type}:${node.value}`];
      })
    ),
  ].sort();
}

describe.each(Object.entries(catalogs))("%s policy guidance", (locale, messages) => {
  const t = createTranslator({ locale, messages, namespace: "shared.access.conditionHelp" });
  const simT = createTranslator({ locale, messages, namespace: "shared.access.simulation" });
  const provider = (children: ReactNode) => (
    <NextIntlClientProvider locale={locale} messages={messages}>
      {children}
    </NextIntlClientProvider>
  );

  it("translates fail-closed attribute guidance while keeping real server catalog metadata and unknown IDs", () => {
    render(
      provider(
        <PolicyConditionHelp
          catalog={catalog}
          kinds={[...catalog.map((entry) => entry.kind), "future_kind_id"]}
        />
      )
    );
    expect(screen.getByRole("region", { name: t("label") })).toBeInTheDocument();
    expect(screen.getByText(t("intro"))).toBeInTheDocument();
    for (const [i, entry] of catalog.entries()) {
      expect(screen.getByText(entry.label)).toBeInTheDocument();
      expect(screen.getByText(entry.kind)).toBeInTheDocument();
      expect(
        screen.getByText(
          `${entry.description} ${i < 4 ? Object.values(messages.shared.access.conditionHelp.needs)[i] : t("unknownNeeds", { needs: "__proto__" })}`
        )
      ).toBeInTheDocument();
    }
    expect(screen.getAllByText("future_kind_id")).toHaveLength(2);
    expect(screen.getByText(t("unknown"))).toBeInTheDocument();
  });

  it("retains catalog failure diagnostics, unknown-until-loaded guidance and empty/loading states", () => {
    const { rerender, container } = render(
      provider(
        <PolicyConditionHelp
          catalog={[]}
          kinds={["opaque_kind"]}
          error={{ message: "RAW_CATALOG_ERROR" }}
        />
      )
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      t("failed", { message: "RAW_CATALOG_ERROR" })
    );
    expect(screen.getByText(t("pending"))).toBeInTheDocument();
    expect(screen.queryByText(t("unknown"))).toBeNull();
    rerender(provider(<PolicyConditionHelp catalog={[]} kinds={["opaque_kind"]} loading />));
    expect(container.querySelector('[aria-busy="true"]')).toBeInTheDocument();
    expect(screen.queryByText(t("unknown"))).toBeNull();
    rerender(provider(<PolicyConditionHelp catalog={[]} kinds={[]} />));
    expect(container).toBeEmptyDOMElement();
  });

  it("translates simulation counts/outcomes without claiming unknown holders are allowed or changing history, links or notes", () => {
    const personHref = vi.fn((id: string) => `/actual-people/${id}`);
    const simulation = {
      ...SIMULATION,
      holders: SIMULATION.holders.map((h, i) => ({
        ...h,
        memberId: `actual-member-${i}`,
        sourceScopeLabel: `RAW_SCOPE_${i}`,
        detail: `RAW_DETAIL_${i}`,
      })),
      notes: ["RAW_OPERATOR_NOTE"],
    };
    render(provider(<PolicySimulationPanel simulation={simulation} personHref={personHref} />));
    expect(screen.getByRole("region", { name: simT("label") })).toBeInTheDocument();
    expect(screen.getByTestId("simulation-summary")).toHaveTextContent(
      simT("holders", { count: simulation.holdersCount, denied: simulation.holdersDeniedCount })
    );
    expect(screen.getByTestId("simulation-summary")).toHaveTextContent(
      simT("unknown", { count: simulation.holdersUnknownCount })
    );
    expect(screen.getAllByText(simT("outcome.UNKNOWN")).length).toBeGreaterThan(0);
    expect(screen.queryByText(simT("outcome.NOT_DENIED"))).toBeNull();
    expect(
      screen.getByText(simT("covers", { actions: simulation.actions.join(", ") }))
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        simT("recorded", {
          days: simulation.windowDays,
          count: simulation.decisionsEvaluated,
          denied: simulation.decisionsDeniedCount,
          unknown: simulation.decisionsUnknownCount,
        })
      )
    ).toBeInTheDocument();
    expect(screen.getByText("RAW_OPERATOR_NOTE")).toBeInTheDocument();
    const affected = simulation.holders.filter((h) => h.outcome !== "NOT_DENIED");
    for (const h of affected) {
      expect(screen.getByText(simT("at", { scope: h.sourceScopeLabel }))).toBeInTheDocument();
      expect(
        screen.getByText(`${[...h.denied, ...h.unknown].join(", ")} · ${h.detail}`)
      ).toBeInTheDocument();
      expect(personHref).toHaveBeenCalledWith(h.memberId);
    }
    const historyRows = screen
      .getByRole("heading", { name: simT("flipped") })
      .parentElement!.querySelectorAll("li");
    expect(historyRows).toHaveLength(simulation.decisions.length);
    for (const [index, decision] of simulation.decisions.entries()) {
      const row = within(historyRows[index]);
      expect(row.getByText(decision.action)).toHaveAttribute("title", decision.action);
      expect(row.getByText(decision.actorDisplay)).toHaveAttribute("title", decision.actorDisplay);
    }
  });

  it("preserves no-audit/no-holders and refusal/retry semantics, plus raw future outcome IDs", () => {
    const retry = vi.fn();
    const { rerender, container } = render(
      provider(<PolicySimulationPanel simulation={SIMULATION_EMPTY} />)
    );
    expect(screen.getByText(simT("none"))).toBeInTheDocument();
    expect(
      screen.getByText(simT("noRecorded", { days: SIMULATION_EMPTY.windowDays }))
    ).toBeInTheDocument();
    expect(screen.queryByText(simT("flipped"))).toBeNull();
    rerender(
      provider(
        <PolicySimulationPanel
          simulation={{ ...SIMULATION, ok: false, errors: ["RAW_REFUSAL_ERROR"] }}
          onRetry={retry}
        />
      )
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      simT("failed", { message: "RAW_REFUSAL_ERROR" })
    );
    expect(screen.queryByTestId("simulation-summary")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: simT("retry") }));
    expect(retry).toHaveBeenCalledTimes(1);
    rerender(
      provider(
        <PolicySimulationPanel
          simulation={{
            ...SIMULATION,
            holders: [{ ...SIMULATION.holders[0], outcome: "FUTURE_OUTCOME_ID" }],
            decisions: [],
          }}
        />
      )
    );
    expect(screen.getByText("FUTURE_OUTCOME_ID")).toBeInTheDocument();
    rerender(provider(<PolicySimulationPanel simulation={null} loading />));
    expect(container.querySelector('[aria-busy="true"]')).toBeInTheDocument();
    rerender(provider(<PolicySimulationPanel simulation={null} />));
    expect(container).toBeEmptyDOMElement();
  });

  it("keeps every ICU plural argument contract and supports single-holder single-day summaries", () => {
    for (const root of ["conditionHelp", "simulation"] as const) {
      const english = leaves(en.shared.access[root]);
      const actual = leaves(messages.shared.access[root]);
      expect(Object.keys(actual)).toEqual(Object.keys(english));
      for (const key of Object.keys(english))
        expect(contract(parse(actual[key]))).toEqual(contract(parse(english[key])));
    }
    const sentence = holderSentence(
      { ...SIMULATION, holdersCount: 1, holdersDeniedCount: 1, holdersUnknownCount: 0 },
      {
        none: simT("none"),
        denied: (count, denied) => simT("holders", { count, denied }),
        unknown: (count) => simT("unknown", { count }),
      }
    );
    expect(sentence).toBe(simT("holders", { count: 1, denied: 1 }));
    expect(simT("noRecorded", { days: 1 })).not.toContain("{days");
    expect(holderSentence(SIMULATION)).toContain("3 of 41 holders would be denied");
  });
});
