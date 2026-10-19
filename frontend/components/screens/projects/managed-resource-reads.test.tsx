import { fireEvent, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import { renderWithIntl, renderWithIntl as render } from "@/test/render-with-intl";
import { render as renderRaw } from "@testing-library/react";
import { fakeController } from "@/components/data-table/fixtures";
import { ManagedResourceDetail } from "./ManagedResourceDetail";
import { ProjectManagedResourceList } from "./ProjectManagedResourceList";
import { RESOURCE, RESOURCE_DETAIL } from "./resource-reads.fixtures";

describe("exact metadata review", () => {
  it.each(["revision", "identity", "missing", "refused"])(
    "refuses %s before rendering writable details",
    (failure) => {
      const review = vi.fn();
      const current =
        failure === "missing"
          ? null
          : {
              ...RESOURCE,
              contextRevision: failure === "revision" ? "changed" : RESOURCE.contextRevision,
              id: failure === "identity" ? "01930000-0000-7000-8000-000000000099" : RESOURCE.id,
            };
      render(
        <ManagedResourceDetail
          {...RESOURCE_DETAIL}
          current={current}
          refused={failure === "refused"}
          onRefresh={review}
        />
      );
      expect(screen.getByRole("alert")).toHaveTextContent("context changed");
      expect(screen.queryByRole("button", { name: "Reprovision" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Attach" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Review again" }));
      expect(review).toHaveBeenCalledOnce();
    }
  );
  it("does not offer detach against consumers held from an in-flight page", () => {
    renderWithIntl(
      <ManagedResourceDetail
        {...RESOURCE_DETAIL}
        attachments={{ ...RESOURCE_DETAIL.attachments, isStale: true }}
      />
    );
    expect(screen.getByRole("button", { name: "Detach" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Attach" })).toBeDisabled();
  });
  it.each(["en", "es", "fr", "de", "ja", "ko", "pt-BR", "zh-Hans"])(
    "localizes known states without translating unknown provider data in %s",
    async (locale) => {
      const messages = (await import(`../../../messages/${locale}.json`)).default;
      const errors: Error[] = [];
      renderRaw(
        <NextIntlClientProvider
          locale={locale}
          messages={messages}
          timeZone="UTC"
          onError={(error) => errors.push(error)}
        >
          <ProjectManagedResourceList
            controller={fakeController({
              rows: [
                RESOURCE,
                {
                  ...RESOURCE,
                  id: "unknown-id",
                  status: "provider_phase_v2",
                  name: "literal-technical-name",
                },
              ],
            })}
            onOpen={() => {}}
          />
        </NextIntlClientProvider>
      );
      expect(screen.getByText(messages.projectResources.reads.statuses.active)).toBeVisible();
      expect(screen.getByText("provider_phase_v2")).toBeVisible();
      expect(screen.getByText("literal-technical-name")).toBeVisible();
      expect(errors).toEqual([]);
    }
  );
});
