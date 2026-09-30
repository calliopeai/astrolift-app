import { render, type RenderOptions } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { Fragment, type ReactNode } from "react";

import messages from "@/messages/en.json";

/** App components always have next-intl. Keep that real context in legacy
 * behavior tests, including their own Apollo/permissions/router wrappers. */
export function renderWithIntl(ui: ReactNode, options?: RenderOptions) {
  const CallerWrapper = options?.wrapper ?? Fragment;
  return render(ui, {
    ...options,
    wrapper: ({ children }) => (
      <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        <CallerWrapper>{children}</CallerWrapper>
      </NextIntlClientProvider>
    ),
  });
}
