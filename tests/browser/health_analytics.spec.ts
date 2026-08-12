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

test("Health Analytics shows deterministic summaries for recorded metrics", async ({
  page,
}) => {
  const marker = Date.now();
  await register(page, `analytics-owner-${marker}@example.com`, "Analytics Owner");
  await page.getByTestId("person-card").first().click();

  const metricForm = page.getByTestId("metric-form");
  await metricForm
    .locator('input[name="recorded_at"]')
    .fill(new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString().slice(0, 16));
  await metricForm.locator('input[name="heart_rate_bpm"]').fill("72");
  await metricForm.locator('input[name="weight_kg"]').fill("70");
  await metricForm.getByRole("button", { name: "Save metric" }).click();
  await expect(page.getByTestId("metric-card")).toHaveCount(1);

  await page.getByTestId("analytics-link").click();
  await expect(page).toHaveURL(/\/analytics\?person_id=/);
  await expect(page.getByRole("heading", { name: "Health Analytics" })).toBeVisible();
  await expect(page.getByTestId("analytics-grid")).toBeVisible();
  await expect(page.getByTestId("analytics-card")).toHaveCount(7);

  const heartRateCard = page.locator(
    '[data-testid="analytics-card"][data-analytics-metric="heart_rate_bpm"]',
  );
  await expect(heartRateCard).toContainText("Latest: 72 bpm");
  await expect(heartRateCard).toContainText("Stable");
  await expect(heartRateCard).toContainText("1 data point(s)");

  await page.getByTestId("analytics-period").selectOption("365");
  await expect(page.getByTestId("analytics-period")).toHaveValue("365");
  await expect(heartRateCard).toContainText("Latest: 72 bpm");
});

test("empty Health Analytics shows an explicit empty state", async ({ page }) => {
  const marker = Date.now();
  await register(page, `empty-analytics-owner-${marker}@example.com`, "Empty Analytics Owner");
  await page.getByTestId("analytics-link").click();

  await expect(page.getByTestId("analytics-grid")).toBeVisible();
  await expect(page.getByTestId("analytics-card")).toHaveCount(7);
  await expect(page.getByTestId("analytics-no-data")).toHaveCount(7);
});
