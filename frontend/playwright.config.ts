import { defineConfig } from "@playwright/test";

// Весь путь: настоящий браузер → Vite → API → PostgreSQL → журнал → поддельная площадка.
const FRONT = 5175;

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  fullyParallel: false,
  timeout: 60_000,
  // Первая загрузка: Vite собирает модули на лету.
  expect: { timeout: 15_000 },
  use: {
    baseURL: `http://localhost:${FRONT}`,
    // Локально берём Chromium; системный Chrome подключается через PLAYWRIGHT_CHROME=1.
    channel: process.env.PLAYWRIGHT_CHROME ? "chrome" : undefined,
    trace: "retain-on-failure",
  },
  webServer: [
    { command: "uv run python -m tests.e2e.serve", cwd: "..", port: 8011, reuseExistingServer: false, timeout: 90_000 },
    {
      command: `npm run dev -- --port ${FRONT} --strictPort`,
      port: FRONT,
      reuseExistingServer: false,
      env: { ADHELPER_API: "http://localhost:8011", ADHELPER_DEMO_SESSION: "../tests/e2e/.state/.demo-session" },
    },
  ],
});
