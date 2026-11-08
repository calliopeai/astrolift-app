import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  testMatch: "nvke-native-journal.spec.ts",
  workers: 1,
  reporter: "list",
  use: { browserName: "chromium", headless: true },
  timeout: 30000,
});
