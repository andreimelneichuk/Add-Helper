import { existsSync, readFileSync } from "node:fs";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Демо-сессия из `adhelper demo-seed`: прокси подставляет её cookie, в браузер токен не попадает.
const DEMO_SESSION = process.env.ADHELPER_DEMO_SESSION ?? new URL("../.demo-session", import.meta.url);
const API = process.env.ADHELPER_API ?? "http://localhost:8010";

function demoCookie(): string | null {
  if (!existsSync(DEMO_SESSION)) return null;
  return (JSON.parse(readFileSync(DEMO_SESSION, "utf8")) as { cookie: string }).cookie;
}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: API,
        configure: (proxy) => {
          proxy.on("proxyReq", (request) => {
            const cookie = demoCookie();
            if (cookie) request.setHeader("cookie", cookie);
          });
        },
      },
    },
  },
  test: { environment: "jsdom", globals: true, setupFiles: "./src/setupTests.ts", exclude: ["e2e/**", "node_modules/**"] },
});
