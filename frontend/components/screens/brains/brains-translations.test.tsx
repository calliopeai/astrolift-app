import { fireEvent, render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import { useLocalListState } from "@/components/list/use-list-state";
import { locales } from "@/i18n/config";
import de from "@/messages/de.json";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";

import { brainMcpConfig } from "./brain-connection";
import { BRAINS, CONNECTION, HOST_PROJECTS } from "./brains.fixtures";
import { BRAINS_LIST } from "./brains-list";
import { BrainsScreen } from "./BrainsScreen";
import type { HostBrainAppValues } from "./HostBrainAppDialog";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };

function Screen({
  rows = BRAINS,
  connection = null,
  onConnect = async () => {},
  hostingSlug = null,
  onHost = () => {},
  onSubmitHost = async () => {},
}: {
  rows?: typeof BRAINS;
  connection?: typeof CONNECTION | null;
  onConnect?: (slug: string) => Promise<void>;
  hostingSlug?: string | null;
  onHost?: (slug: string) => void;
  onSubmitHost?: (values: HostBrainAppValues) => Promise<void>;
}) {
  const list = useLocalListState(BRAINS_LIST);
  return (
    <BrainsScreen
      list={list}
      rows={rows}
      totalCount={rows.length}
      loading={false}
      stale={false}
      error={null}
      onRetry={() => {}}
      connection={connection}
      connecting={false}
      onConnect={onConnect}
      onDismissConnection={() => {}}
      hostingSlug={hostingSlug}
      hostProjects={HOST_PROJECTS}
      hostSubmitting={false}
      onHost={onHost}
      onDismissHost={() => {}}
      onSubmitHost={onSubmitHost}
    />
  );
}

describe("semantic layers (#2320)", () => {
  it.each(locales)("%s renders every key without falling back to English", (locale) => {
    const onError = vi.fn();
    const copy = catalogs[locale].brains;
    expect(Object.keys(copy).sort()).toEqual(Object.keys(en.brains).sort());
    render(
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        timeZone="UTC"
        onError={onError}
      >
        <Screen connection={CONNECTION} />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(copy.connectDialog.config)).toBeInTheDocument();
    expect(screen.getAllByText(copy.title).length).toBeGreaterThan(0);
    expect(screen.getByText(copy.notServed)).toBeInTheDocument();
    expect(screen.getByText(copy.notShared)).toBeInTheDocument();
    if (locale !== "en") expect(copy.context).not.toBe(en.brains.context);
    expect(onError).not.toHaveBeenCalled();
  });

  it("links each layer's endpoints, shares and access, and says when there are none", () => {
    render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <Screen />
      </NextIntlClientProvider>
    );
    const endpoint = screen.getByText("production: https://brain.acme.example");
    expect(endpoint.closest("a")).toHaveAttribute("href", "https://brain.acme.example");
    expect(screen.getByText("medops")).toHaveAttribute("title", "medops: viewer");
    expect(screen.getAllByText(en.brains.manageAccess)[0].closest("a")).toHaveAttribute(
      "href",
      "/apps/leo-brain/access"
    );
  });

  it("connects a served layer and not one that is not served", () => {
    const onConnect = vi.fn(async () => {});
    render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <Screen onConnect={onConnect} />
      </NextIntlClientProvider>
    );
    const [served, notServed] = screen.getAllByRole("button", { name: en.brains.connect });
    expect(notServed).toBeDisabled();
    fireEvent.click(served);
    expect(onConnect).toHaveBeenCalledWith("leo-brain");
  });

  it("shows the issued token once as an MCP client config", () => {
    render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <Screen connection={CONNECTION} />
      </NextIntlClientProvider>
    );
    const dialog = within(screen.getByRole("alertdialog"));
    expect(dialog.getByText("https://brain.acme.example/mcp")).toBeInTheDocument();
    expect(JSON.parse(brainMcpConfig(CONNECTION))).toEqual({
      mcpServers: {
        "leo-brain": {
          type: "http",
          url: "https://brain.acme.example/mcp",
          headers: { "X-Astrolift-Brain-Token": CONNECTION.token },
        },
      },
    });
    expect(
      dialog.getByText((_, el) => el?.textContent?.startsWith('{\n  "mcpServers"') ?? false, {
        selector: "div",
      })
    ).toHaveTextContent(CONNECTION.token);
  });

  it.each(locales)("%s renders the host dialog without falling back to English", (locale) => {
    const onError = vi.fn();
    const copy = catalogs[locale].brains;
    render(
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        timeZone="UTC"
        onError={onError}
      >
        <Screen hostingSlug="leo-brain" />
      </NextIntlClientProvider>
    );
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText(copy.hostDialog.envHelp)).toBeInTheDocument();
    expect(dialog.getByRole("button", { name: copy.hostDialog.create })).toBeDisabled();
    if (locale !== "en")
      expect(copy.hostDialog.description).not.toBe(en.brains.hostDialog.description);
    expect(onError).not.toHaveBeenCalled();
  });

  it("hosts an app on a served layer from the template", () => {
    const onHost = vi.fn();
    const onSubmitHost = vi.fn(async () => {});
    const { rerender } = render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <Screen onHost={onHost} />
      </NextIntlClientProvider>
    );
    const [served, notServed] = screen.getAllByRole("button", { name: en.brains.host });
    expect(notServed).toBeDisabled();
    fireEvent.click(served);
    expect(onHost).toHaveBeenCalledWith("leo-brain");

    rerender(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <Screen hostingSlug="leo-brain" onSubmitHost={onSubmitHost} />
      </NextIntlClientProvider>
    );
    const dialog = within(screen.getByRole("dialog"));
    fireEvent.change(dialog.getByLabelText(en.brains.hostDialog.image), {
      target: { value: " ghcr.io/acme/brain-chat:1.0 " },
    });
    fireEvent.change(dialog.getByLabelText(en.brains.hostDialog.port), {
      target: { value: "3000" },
    });
    fireEvent.click(dialog.getByRole("button", { name: en.brains.hostDialog.create }));
    expect(onSubmitHost).toHaveBeenCalledWith({
      projectId: HOST_PROJECTS[0].id,
      image: "ghcr.io/acme/brain-chat:1.0",
      name: "",
      port: 3000,
    });
  });

  it("shows the empty state when nothing is readable", () => {
    render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <Screen rows={[]} />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(en.brains.emptyTitle)).toBeInTheDocument();
  });
});
