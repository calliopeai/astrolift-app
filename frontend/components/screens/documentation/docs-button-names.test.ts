import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { locales } from "@/i18n/config";

/**
 * The docs tell people which button to press; in every language that has to
 * be the label the button actually shows (#2381).
 */
const catalog = (locale: string) =>
  JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8"));

describe.each(locales)("%s documentation names the settings buttons", (locale) => {
  const c = catalog(locale);
  const connectGithub = c.sourceProviders.hosts.connectGithub as string;
  const newProvider = c.identityProviders.list.newProvider as string;
  const makeActive = c.identityProviders.list.makeActive as string;

  it("as the source providers screen labels Connect to GitHub", () => {
    expect(c.docsSourceProviders.openProvidersGitHub).toContain(
      `<strong>${connectGithub}</strong>`
    );
  });

  it("as the identity providers screen labels New provider and Make active", () => {
    expect(c.docsIdentityProviders.configureBody).toContain(`<strong>${newProvider}</strong>`);
    expect(c.docsIdentityProviders.configureBody).toContain(`<strong>${makeActive}</strong>`);
    expect(c.docsIdentityProviders.prereqRedirect).toContain(`<bold>${newProvider}</bold>`);
    for (const kind of ["oidc", "cognito", "auth0", "okta", "google", "github", "saml"]) {
      expect(c.docsIdentityProviders.kinds[kind].steps.s3).toContain(newProvider);
    }
  });
});
