import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const config = [
  ...nextVitals,
  ...nextTs,
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts", "playwright-report/**", "test-results/**"] },
  // The container entrypoint wraps Next's CommonJS standalone server.js, so it is CommonJS too.
  { files: ["edge.js"], rules: { "@typescript-eslint/no-require-imports": "off" } },
];

export default config;
