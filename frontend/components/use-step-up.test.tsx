import { MockedProvider } from "@apollo/client/testing/react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { useEffect } from "react";
import { expect, it } from "vitest";
import messages from "@/messages/en.json";
import { ELEVATE_ADMIN_SESSION } from "@/graphql/identity/identity.mutations";
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

const hookRef: { current: ReturnType<typeof useStepUp> | null } = { current: null };

function Probe() {
  const stepUp = useStepUp();
  useEffect(() => {
    hookRef.current = stepUp;
  });
  return <output>{stepUp.open ? stepUp.method : "closed"}</output>;
}

const hook = () => hookRef.current!;

function mount(supportedMethods: string[], extra: unknown[] = []) {
  render(
    <MockedProvider mocks={[status(supportedMethods), ...(extra as never[])]}>
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

it("offers the step-up PIN first when the session lists it (#2399)", async () => {
  mount(["pin", "sso"]);
  refuse({});
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("pin"));
  expect(hook().fallbackMethod).toBe("sso");
});

it("keeps a PIN the refusal carries", async () => {
  mount(["sso"]);
  refuse({ supportedMethods: ["pin", "password"] });
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("pin"));
  expect(hook().fallbackMethod).toBe("password");
});

it("falls back to the login method when the person declines the PIN", async () => {
  mount(["pin", "sso"]);
  refuse({});
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("pin"));
  act(() => hook().onDeclinePin());
  expect(screen.getByRole("status").textContent).toBe("sso");
});

it("falls back once a wrong PIN locks it", async () => {
  const wrongPin = {
    request: { query: ELEVATE_ADMIN_SESSION, variables: { input: { method: "pin", credential: "000000" } } },
    result: {
      data: {
        elevateAdminSession: {
          __typename: "MutationResult",
          ok: false,
          errors: [{ __typename: "MutationError", code: "PERMISSION_DENIED", message: "invalid credential", field: "credential" }],
          data: null,
        },
      },
    },
  };
  mount(["pin", "sso"], [wrongPin, status(["sso"])]);
  refuse({});
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("pin"));
  let elevated = true;
  await act(async () => {
    elevated = await hook().onSubmitPin("000000");
  });
  expect(elevated).toBe(false);
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("sso"));
  expect(hook().error).toBe(messages.stepUp.errors.pinLocked);
});

it("refuses a malformed PIN without sending it", async () => {
  mount(["pin", "sso"]);
  refuse({});
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("pin"));
  await act(async () => {
    await hook().onSubmitPin("12");
  });
  expect(hook().error).toBe(messages.stepUp.errors.pinFormat);
  expect(screen.getByRole("status").textContent).toBe("pin");
});
