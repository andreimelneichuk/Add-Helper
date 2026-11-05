import { existsSync, readFileSync } from "node:fs";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Демо-сессия из `adhelper demo-seed`: прокси подставляет её cookie, в браузер токен не попадает.
const DEMO_SESSION = process.env.ADHELPER_DEMO_SESSION ?? new URL("../.demo-session", import.meta.url);
const API = process.env.ADHELPER_API ?? "http://localhost:8000";

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
            // Свой вход браузера (сквозной тест второго владельца) важнее демо-входа,
            // а `ah_guest` — гость стенда, который сам войдёт через подставной Яндекс ID.
            const sent = request.getHeader("cookie")?.toString() ?? "";
            if (/(^|;\s*)(ah_session|ah_guest)=/.test(sent)) return;
            const cookie = demoCookie();
            // Дописываем, а не заменяем: cookie потока входа и возврата нужны сервису.
            if (cookie) request.setHeader("cookie", sent ? `${sent}; ${cookie}` : cookie);
          });
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/setupTests.ts",
    exclude: ["e2e/**", "node_modules/**"],
    testTimeout: 20_000,
    hookTimeout: 20_000,
    maxWorkers: 4,
    maxConcurrency: 4,
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "json-summary"],
      reportsDirectory: "./coverage",
      include: ["src/**"],
      exclude: [
        "src/main.tsx",
        "src/setupTests.ts",
        "src/app/icons.tsx",
        "src/app/data.ts",
        "src/app/testLive.ts",
        "src/**/*.test.{ts,tsx}",
        "src/**/*.d.ts",
        "src/**/*.css",
      ],
    },
  },
});
