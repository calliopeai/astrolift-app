import { expect, test } from "@playwright/test";

test("Spanish registration validates locally without echoing malformed credentials", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(
    "/iframe.html?id=screens-clusters-list-registerclusterpage--spanish-validation&viewMode=story"
  );
  // The story's interaction reaches malformed JSON; then recover in the browser.
  await expect(page.getByText("El JSON de autenticación no es válido.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Registrar clúster" })).toBeVisible();
  await expect(page.getByRole("alert")).not.toContainText("INVALID_CREDENTIAL_MARKER");
  await page.getByLabel("Configuración de autenticación (JSON)").fill('{"token":"SECRET_LITERAL"}');
  await page.getByLabel("Método de autenticación").click();
  await page.getByRole("option", { name: "service_account_token", exact: true }).click();
  await expect(page.getByLabel("Configuración de autenticación (JSON)")).toHaveValue(
    '{"token":"SECRET_LITERAL"}'
  );
  await expect(page.getByText(/Claves: token/)).toBeVisible();
  await page.getByRole("button", { name: "Atrás", exact: true }).click();
  await expect(page.getByLabel("Nombre visible")).toHaveValue("literal-cluster");
  await page.getByRole("button", { name: "Continuar", exact: true }).click();
  await expect(page.getByLabel("Configuración de autenticación (JSON)")).toHaveValue(
    '{"token":"SECRET_LITERAL"}'
  );
  expect(errors).toEqual([]);
});
