import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

const defaultDatabasePath = resolve(process.cwd(), ".healthy-test.db");
const databaseUrl =
  process.env.HEALTHY_DATABASE_URL ??
  `sqlite+pysqlite:///${defaultDatabasePath}`;

export default defineConfig({
  testDir: ".",
  testMatch: [
    "identity.spec.ts",
    "health_metrics.spec.ts",
    "symptom_logs.spec.ts",
    "health_actions.spec.ts",
    "health_assistant_today.spec.ts",
    "health_reports.spec.ts",
    "health_history.spec.ts",
    "health_analytics.spec.ts",
    "height_profile.spec.ts",
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
        HEALTHY_EMAIL_NOTIFICATIONS_ENABLED: "true",
        HEALTHY_SMTP_HOST: "smtp.invalid",
        HEALTHY_SMTP_FROM_ADDRESS: "no-reply@healthy.invalid",
        HEALTHY_SMTP_STARTTLS: "true",
      },
    },
    {
      command: "npm run start --workspace @healthy/web",
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
