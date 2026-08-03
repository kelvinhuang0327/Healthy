import { defineConfig } from "@playwright/test";

const databaseUrl =
  process.env.HEALTHY_DATABASE_URL ??
  "postgresql+psycopg://healthy@127.0.0.1:55432/healthy_test";

export default defineConfig({
  testDir: ".",
  testMatch: [
    "identity.spec.ts",
    "health_metrics.spec.ts",
    "symptom_logs.spec.ts",
    "health_actions.spec.ts",
    "health_assistant_today.spec.ts",
    "health_reports.spec.ts",
  ],

  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:3000",
    browserName: "chromium",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command:
        "uv run --directory apps/api uvicorn healthy.main:app --host 127.0.0.1 --port 8000",
      cwd: process.cwd(),
      url: "http://127.0.0.1:8000/v1/session",
      reuseExistingServer: false,
      timeout: 30_000,
      env: {
        HEALTHY_ENV: "test",
        HEALTHY_DATABASE_URL: databaseUrl,
        HEALTHY_COOKIE_SECURE: "false",
        HEALTHY_ALLOWED_ORIGINS: "http://127.0.0.1:3000",
      },
    },
    {
      command: "npm run web:dev",
      cwd: process.cwd(),
      url: "http://127.0.0.1:3000",
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        HEALTHY_API_ORIGIN: "http://127.0.0.1:8000",
      },
    },
  ],
});
