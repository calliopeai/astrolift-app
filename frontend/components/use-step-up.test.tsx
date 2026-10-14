import { MockedProvider } from "@apollo/client/testing/react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { expect, it } from "vitest";
import messages from "@/messages/en.json";
import { GET_ELEVATION_STATUS } from "@/graphql/identity/identity.queries";
import { STEP_UP_EVENT, type StepUpEventDetail } from "@/lib/auth/step-up-events";
import { useStepUp } from "./use-step-up";

function status(supportedMethods: string[]) {
  return {
    request: { query: GET_ELEVATION_STATUS },
    result: {
      data: {
        astroliftElevationStatus: {
          __typename: "AstroliftElevationStatus",
          elevated: false,
          elevatedUntil: null,
          secondsRemaining: 0,
          method: null,
          requiredFor: [],
          supportedMethods,
        },
      },
    },
  };
}

function Probe() {
  const { open, method } = useStepUp();
  return <output>{open ? method : "closed"}</output>;
}

function mount(supportedMethods: string[]) {
  render(
    <MockedProvider mocks={[status(supportedMethods)]}>
      <NextIntlClientProvider locale="en" messages={messages}>
        <Probe />
      </NextIntlClientProvider>
    </MockedProvider>
  );
}

function refuse(detail: Partial<StepUpEventDetail>) {
  act(() => {
    window.dispatchEvent(
      new CustomEvent(STEP_UP_EVENT, { detail: { operationName: "StartDeployment", message: "", ...detail } })
    );
  });
}

it("offers the IdP to an SSO session when the refusal does not name a method", async () => {
  mount(["sso"]);
  refuse({});
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("sso"));
});

it("keeps the password form for a password session", async () => {
  mount(["password"]);
  refuse({});
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("password"));
});

it("still prefers the methods the refusal carries", async () => {
  mount(["password"]);
  refuse({ supportedMethods: ["sso"] });
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("sso"));
});
