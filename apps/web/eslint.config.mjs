import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    files: ["mock/**/*.mjs", "e2e/**/*.mjs"],
    languageOptions: { globals: { process: "readonly", console: "readonly", Buffer: "readonly", URL: "readonly", setTimeout: "readonly", clearTimeout: "readonly", setInterval: "readonly", clearInterval: "readonly" } },
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts", "screenshots/**", "test-results/**"]),
]);
