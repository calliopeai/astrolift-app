import { readFileSync } from "node:fs";
import { fireEvent, render, screen } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { PipelineDag } from "@/components/viz/pipeline-dag";

const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
describe.each(locales)("shared pipeline graph (%s)", (locale) => {
  it("localizes actual nodes, durations and navigation while preserving click/configuration identities", () => {
    const t = createTranslator({
      locale,
      messages: catalogs[locale],
      namespace: "shared.pipelineGraph",
    });
    const errors = vi.fn(),
      selected = vi.fn();
    const stages = [
      {
        id: "literal-job",
        name: "literal-node",
        status: "running",
        needs: [],
        href: "/literal/stage",
        startedAt: "2026-10-02T10:00:00Z",
      },
      {
        id: "literal-child",
        name: "literal-nested",
        status: "success",
        needs: ["literal-job"],
        topologyKind: "subflow" as const,
        startedAt: "2026-10-02T10:00:00Z",
        finishedAt: "2026-10-02T10:01:05Z",
      },
      { id: "opaque-job", name: "literal-unknown", status: "FUTURE_STATUS_v2", needs: [] },
    ];
    render(
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        timeZone="UTC"
        onError={errors}
      >
        <PipelineDag stages={stages} onStageClick={selected} animateActiveEdges />
      </NextIntlClientProvider>
    );
    expect(screen.getByLabelText(t("label"))).toBeInTheDocument();
    // React Flow's nodes await DOM measurement in jsdom; their real renderer is mounted.
    const link = screen.getByTitle(t("open", { name: "literal-node" }));
    expect(link).toHaveAttribute("href", "/literal/stage");
    expect(link).toHaveAttribute("title", t("open", { name: "literal-node" }));
    expect(screen.getByText(t("nestedWorkflow"))).toBeInTheDocument();
    expect(screen.getByText(t("minutesSeconds", { minutes: 1, seconds: 5 }))).toBeInTheDocument();
    expect(screen.getByText("FUTURE_STATUS_v2")).toBeInTheDocument();
    link.addEventListener("click", (event) => event.preventDefault());
    fireEvent.click(link);
    expect(selected).toHaveBeenCalledWith(stages[0]);
    expect(stages[0].status).toBe("running");
    expect(errors).not.toHaveBeenCalled();
  });
});
