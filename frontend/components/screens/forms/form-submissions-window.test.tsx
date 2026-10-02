import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, expect, it, vi } from "vitest";

import messages from "@/messages/en.json";
import { FormSubmissionsTab } from "./FormSubmissionsTab";
import { SUBMISSIONS } from "./forms.fixtures";

afterEach(() => vi.useRealTimers());

it("updates the actual chart window when the viewer changes time zone", () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-01-31T15:30:00Z"));
  const submissions = ["2026-01-02T14:59:59Z", "2026-01-31T15:00:00Z"].map(
    (submittedAt, index) => ({ ...SUBMISSIONS[0], id: `submission-${index}`, submittedAt })
  );
  const viewIn = (timeZone: string) => (
    <NextIntlClientProvider locale="en" messages={messages} timeZone={timeZone}>
      <FormSubmissionsTab submissions={submissions} loading={false} />
    </NextIntlClientProvider>
  );
  const view = render(viewIn("Asia/Tokyo"));
  expect(screen.getByText("1 new in window")).toBeVisible();
  view.rerender(viewIn("UTC"));
  expect(screen.getByText("2 new in window")).toBeVisible();
});
