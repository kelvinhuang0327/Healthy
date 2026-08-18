import { expect, test, type Page } from "@playwright/test";

const password = "Synthetic-Only-Password-42";

async function register(
  page: Page,
  email: string,
  displayName: string,
): Promise<string> {
  await page.goto("/");
  await page.getByTestId("register-form").getByLabel("Email").fill(email);
  await page.getByTestId("register-form").getByLabel("Password").fill(password);
  await page
    .getByTestId("register-form")
    .getByLabel("Your display name")
    .fill(displayName);
  await page.getByRole("button", { name: "Register securely" }).click();
  await expect(page.getByText("Session active")).toBeVisible();
  const defaultPerson = page.getByTestId("person-card").first();
  await expect(defaultPerson).toContainText(displayName);
  return (await defaultPerson.getAttribute("data-person-id")) ?? "";
}

test("selected Person can log a health metric and see it in newest-first history", async ({
  page,
}) => {
  const marker = Date.now();
  const email = `metrics-owner-${marker}@example.com`;
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource:")) {
      consoleErrors.push(message.text());
    }
  });

  const personId = await register(page, email, "Metrics Owner");
  await page.getByTestId("person-card").first().click();
  await expect(page.getByTestId("selected-person-pill")).toBeVisible();
  await expect(page.getByTestId("health-score-status")).toHaveText("stable");

  const form = page.getByTestId("metric-form");
  await form.locator('input[name="recorded_at"]').fill("2026-08-09T08:30");
  await form.locator('input[name="systolic_bp_mm_hg"]').fill("120");
  await form.locator('input[name="diastolic_bp_mm_hg"]').fill("80");
  await form.locator('input[name="heart_rate_bpm"]').fill("72");
  await form.locator('input[name="steps"]').fill("6000");
  await form.locator('input[name="weight_kg"]').fill("70.25");
  await form.locator('input[name="blood_glucose_mg_dl"]').fill("95.5");
  await form.locator('input[name="sleep_hours"]').fill("7.25");
  await form.locator('input[name="note"]').fill("After breakfast");
  await form.getByRole("button", { name: "Save metric" }).click();

  const metricList = page.getByTestId("metric-list");
  await expect(metricList.getByTestId("metric-card")).toHaveCount(1);
  const firstCard = metricList.getByTestId("metric-card").first();
  await expect(firstCard).toContainText("120/80 mmHg");
  await expect(firstCard).toContainText("72 bpm");
  await expect(firstCard).toContainText("6000 steps");
  await expect(firstCard).toContainText("70.25 kg");
  await expect(firstCard).toContainText("95.5 mg/dL");
  await expect(firstCard).toContainText("7.25 hours");
  await expect(firstCard).toContainText("2026");
  await expect(firstCard).toContainText("After breakfast");
  await expect(page.getByTestId("health-score-value")).toHaveText("97");
  await expect(page.getByTestId("health-score-status")).toHaveText("stable");
  await expect(page.getByTestId("health-score-coverage")).toContainText("Evaluated:");
  await expect(page.getByTestId("health-score-coverage")).toContainText(
    "Unavailable / not evaluated",
  );

  await form.locator('input[name="heart_rate_bpm"]').fill("65");
  await form.getByRole("button", { name: "Save metric" }).click();
  await expect(metricList.getByTestId("metric-card")).toHaveCount(2);
  const cards = metricList.getByTestId("metric-card");
  await expect(cards.nth(0)).toContainText("65 bpm");
  await expect(cards.nth(1)).toContainText("72 bpm");

  await page.reload();
  const reloadedMetricList = page.getByTestId("metric-list");
  await expect(reloadedMetricList.getByTestId("metric-card")).toHaveCount(2);
  await expect(
    reloadedMetricList.getByTestId("metric-card").filter({ hasText: "7.25 hours" }),
  ).toHaveCount(1);
  await expect(reloadedMetricList).not.toContainText(/recommend|quality|target|good|poor/i);

  const foreignPersonStatus = await page.evaluate(async (id) => {
    const response = await fetch(`/api/v1/persons/${id}/metrics`, {
      credentials: "same-origin",
    });
    return response.status;
  }, personId);
  expect(foreignPersonStatus).toBe(200);

  expect(consoleErrors).toEqual([]);
});

test("selected Person can import health metrics from CSV, see provenance badges and idempotency summary", async ({
  page,
}) => {
  const marker = Date.now();
  const email = `csv-metrics-${marker}@example.com`;
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource:")) {
      consoleErrors.push(message.text());
    }
  });

  await register(page, email, "CSV Import Owner");
  await page.getByTestId("person-card").first().click();
  await expect(page.getByTestId("selected-person-pill")).toBeVisible();

  // 1. First import via CSV text area
  const csvImportCard = page.getByTestId("metric-csv-import-card");
  await expect(csvImportCard).toBeVisible();
  const csvData = [
    "recorded_at,systolic_bp_mm_hg,diastolic_bp_mm_hg,heart_rate_bpm,steps,weight_kg,blood_glucose_mg_dl,sleep_hours,note",
    "2026-08-01T08:00:00Z,120,80,72,8000,70.50,95.5,7.50,Imported Morning Check",
    "2026-08-01T12:00:00Z,,,,,70.40,,,Imported Lunch Check",
  ].join("\n");

  await page.getByTestId("metric-csv-text-input").fill(csvData);
  await page.getByTestId("metric-csv-submit-button").click();

  // 2. Summary visible
  const summary = page.getByTestId("metric-csv-summary");
  await expect(summary).toBeVisible();
  await expect(summary).toContainText("2 imported");
  await expect(summary).toContainText("0 duplicate/existing");

  // 3. Metric cards display provenance
  const metricList = page.getByTestId("metric-list");
  await expect(metricList.getByTestId("metric-card")).toHaveCount(2);
  const badges = metricList.getByTestId("metric-source-badge");
  await expect(badges.first()).toHaveText("Imported CSV");

  // 4. Log manual metric
  const manualForm = page.getByTestId("metric-form");
  await manualForm.locator('input[name="heart_rate_bpm"]').fill("68");
  await manualForm.getByRole("button", { name: "Save metric" }).click();
  await expect(metricList.getByTestId("metric-card")).toHaveCount(3);
  await expect(metricList.getByTestId("metric-source-badge").first()).toHaveText("Manual");

  // 5. Re-import same CSV -> 0 imported, 2 duplicate
  await page.getByTestId("metric-csv-text-input").fill(csvData);
  await page.getByTestId("metric-csv-submit-button").click();
  await expect(summary).toContainText("0 imported");
  await expect(summary).toContainText("2 duplicate/existing");
  await expect(metricList.getByTestId("metric-card")).toHaveCount(3);

  // 6. Reload and check localStorage does not contain raw CSV
  await page.reload();
  const localStorageKeys = await page.evaluate(() => Object.keys(localStorage));
  expect(localStorageKeys.filter((k) => k.toLowerCase().includes("csv"))).toEqual([]);

  expect(consoleErrors).toEqual([]);
});
