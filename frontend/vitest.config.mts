import path from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react-swc";
import { defineConfig } from "vitest/config";

const rootDir = path.dirname(fileURLToPath(import.meta.url));

// The Next.js app resolves the ``@/*`` path alias (tsconfig ``paths``) to the
// frontend root. Vitest doesn't read tsconfig, so mirror that single alias
// here. Matching a leading ``@/`` (not a bare ``@``) leaves scoped npm
// packages such as ``@apollo/*`` and ``@testing-library/*`` untouched.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: [{ find: /^@\//, replacement: `${rootDir}/` }],
  },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./test/setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**", ".next/**"],
  },
});
