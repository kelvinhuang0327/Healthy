import { expect, test, type Page } from "@playwright/test";

const password = "Synthetic-Only-Password-42";

async function register(
  page: Page,
  email: string,
  displayName: string,
): Promise<void> {
  await page.goto("/");
  await page.getByTestId("register-form").getByLabel("Email").fill(email);
  await page.getByTestId("register-form").getByLabel("Password").fill(password);
  await page
    .getByTestId("register-form")
    .getByLabel("Your display name")
    .fill(displayName);
  await page.getByRole("button", { name: "Register securely" }).click();
  await expect(page.getByText("Session active")).toBeVisible();
}

test("Health History shows mixed sources in order and filters by type", async ({
  page,
}) => {
  const marker = Date.now();
  await register(page, `history-owner-${marker}@example.com`, "History Owner");
  await page.getByTestId("person-card").first().click();
  await expect(page.getByTestId("selected-person-pill")).toBeVisible();

  const metricTime = new Date(Date.now() - 3 * 24 * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 16);
  const symptomTime = new Date(Date.now() - 2 * 24 * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 16);
  const reportTime = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();

  const symptomForm = page.getByTestId("symptom-form");
  await symptomForm.getByLabel("Symptom").fill("History headache");
  await symptomForm.locator('input[name="occurred_at"]').fill(symptomTime);
  await symptomForm.getByLabel("Severity (1-5)").fill("3");
  await symptomForm.getByRole("button", { name: "Save symptom" }).click();
  await expect(page.getByTestId("symptom-list").getByTestId("symptom-card")).toHaveCount(1);

  const metricForm = page.getByTestId("metric-form");
  await metricForm.locator('input[name="recorded_at"]').fill(metricTime);
  await metricForm.locator('input[name="heart_rate_bpm"]').fill("72");
  await metricForm.getByRole("button", { name: "Save metric" }).click();
  await expect(page.getByTestId("metric-list").getByTestId("metric-card")).toHaveCount(1);

  const reportForm = page.getByTestId("report-import-form");
  await reportForm
    .getByLabel(/JSON report/)
    .fill(
      JSON.stringify({
        schema_version: "healthy.health-report.v1",
        source_name: "Pending History Lab",
        reported_at: reportTime,
        observations: [
          {
            code: "PENDING_HISTORY_GLUCOSE",
            display_name: "Pending history glucose",
            value_numeric: 101,
            unit: "mg/dL",
            observed_at: reportTime,
          },
        ],
      }),
    );
  await reportForm.getByRole("button", { name: "Import structured report" }).click();
  await expect(
    page.getByTestId("report-card").filter({ hasText: "Pending History Lab" }),
  ).toHaveAttribute("data-report-status", "pending");

  await reportForm
    .getByLabel(/JSON report/)
    .fill(
      JSON.stringify({
        schema_version: "healthy.health-report.v1",
        source_name: "Confirmed History Lab",
        reported_at: reportTime,
        observations: [
          {
            code: "CONFIRMED_HISTORY_GLUCOSE",
            display_name: "Confirmed history glucose",
            value_numeric: 95.5,
            unit: "mg/dL",
            observed_at: reportTime,
          },
        ],
      }),
    );
  await reportForm.getByRole("button", { name: "Import structured report" }).click();
  const confirmedCard = page
    .getByTestId("report-card")
    .filter({ hasText: "Confirmed History Lab" });
  await expect(confirmedCard).toHaveAttribute("data-report-status", "pending");
  await confirmedCard.getByTestId("confirm-report-button").click();
  await expect(confirmedCard).toHaveAttribute("data-report-status", "confirmed");

  await page.getByTestId("history-link").click();
  await expect(page).toHaveURL(/\/history\?person_id=/);
  await expect(page.getByRole("heading", { name: "Health History" })).toBeVisible();

  const historyItems = page.getByTestId("history-item");
  await expect(historyItems).toHaveCount(3);
  await expect(historyItems.nth(0)).toHaveAttribute("data-history-kind", "report_observation");
  await expect(historyItems.nth(0)).toContainText("Confirmed history glucose");
  await expect(historyItems.nth(0)).toContainText("Confirmed History Lab");
  await expect(historyItems.nth(1)).toHaveAttribute("data-history-kind", "symptom");
  await expect(historyItems.nth(1)).toContainText("History headache");
  await expect(historyItems.nth(2)).toHaveAttribute("data-history-kind", "metric");
  await expect(historyItems.nth(2)).toContainText("72 bpm");
  await expect(page.getByText("Pending history glucose")).toHaveCount(0);

  await page.getByTestId("history-filter-report_observation").click();
  await expect(page.getByTestId("history-item")).toHaveCount(1);
  await expect(page.getByTestId("history-item").first()).toContainText(
    "Confirmed history glucose",
  );
  await page.getByTestId("history-filter-all").click();
  await expect(page.getByTestId("history-item")).toHaveCount(3);
});

test("empty Health History shows an explicit empty state", async ({ page }) => {
  const marker = Date.now();
  await register(page, `empty-history-owner-${marker}@example.com`, "Empty History Owner");
  await page.getByTestId("history-link").click();
  await expect(page.getByTestId("history-empty")).toBeVisible();
  await expect(page.getByTestId("history-empty")).toContainText("No health history yet");
});
