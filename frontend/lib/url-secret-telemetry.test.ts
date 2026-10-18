import { describe, expect, it } from "vitest";
import { redactUrlSecrets, scrubUrlSecrets } from "./url-secret-telemetry";

const SECRET = "secret-canary";

describe("URL secrets in Sentry events (#2440)", () => {
  it.each([
    `https://astro.example/auth/callback?code=${SECRET}`,
    `https://astro.example/app/auth1/callback?state=s&code=${SECRET}`,
    `/auth/callback?token=Session%20${SECRET}`,
    `/x?refresh_token=${SECRET}&page=2`,
    `/auth/invitation/${SECRET}`,
    `/login?next=%2Fauth%2Finvitation%2F${SECRET}`,
    `/login?next=%252Fauth%252Fcallback%253Fcode%253D${SECRET}`,
  ])("redacts %s", (url) => {
    expect(redactUrlSecrets(url)).not.toContain(SECRET);
  });

  it("keeps what is not a credential", () => {
    const url = "https://astro.example/apps?page=2&search=token&exit_code=1";
    expect(redactUrlSecrets(url)).toBe(url);
  });

  it("scrubs every URL in an event, the query string in each of its shapes", () => {
    const event = {
      request: {
        url: `https://astro.example/auth/callback?code=${SECRET}`,
        query_string: `code=${SECRET}&state=s`,
        headers: { Referer: `https://astro.example/auth/invitation/${SECRET}` },
      },
      breadcrumbs: [{ category: "navigation", data: { to: `/auth/callback?code=${SECRET}` } }],
      spans: [{ description: `GET /auth/callback?code=${SECRET}` }],
    };
    const scrubbed = scrubUrlSecrets(event);
    expect(JSON.stringify(scrubbed)).not.toContain(SECRET);
    expect(scrubbed.request.query_string).toBe("code=***REDACTED***&state=s");
  });

  it.each([
    [
      { code: SECRET, page: "2" },
      { code: "***REDACTED***", page: "2" },
    ],
    [
      [
        ["invite_token", SECRET],
        ["page", "2"],
      ],
      [
        ["invite_token", "***REDACTED***"],
        ["page", "2"],
      ],
    ],
  ])("scrubs a query string given as %j", (query, expected) => {
    expect(scrubUrlSecrets({ request: { query_string: query } }).request.query_string).toEqual(
      expected
    );
  });

  it("keeps an error code that is not in a URL", () => {
    const event = { extra: { code: "PERMISSION_DENIED" } };
    expect(scrubUrlSecrets(event).extra.code).toBe("PERMISSION_DENIED");
  });

  it("passes null through and tolerates circular metadata", () => {
    expect(scrubUrlSecrets(null)).toBeNull();
    const record: { url: string; self?: unknown } = { url: `/auth/callback?code=${SECRET}` };
    record.self = record;
    expect(scrubUrlSecrets(record).url).toBe("/auth/callback?code=***REDACTED***");
  });
});
