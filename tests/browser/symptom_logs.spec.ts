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

test("selected Person can backdate symptoms and see a newest-first timeline", async ({
  page,
}) => {
  const marker = Date.now();
  const email = `symptoms-owner-${marker}@example.com`;
  await register(page, email, "Symptoms Owner");

  await page.getByTestId("person-card").first().click();
  await expect(page.getByTestId("selected-person-pill")).toBeVisible();

  const symptomForm = page.getByTestId("symptom-form");
  await symptomForm.getByLabel("Symptom").fill("Headache");
  await symptomForm.locator('input[name="occurred_at"]').fill("2026-07-20T09:30");
  await symptomForm.getByLabel("Severity (1-5)").fill("2");
  await symptomForm.getByLabel("Duration (minutes, optional)").fill("45");
  await symptomForm.getByLabel("Estimated start date (optional)").fill("2026-01-01");
  await symptomForm.getByLabel("Estimated duration (days, optional)").fill("240");
  await symptomForm.getByLabel("Note").fill("Backdated first symptom");
  await symptomForm.getByRole("button", { name: "Save symptom" }).click();

  const timeline = page.getByTestId("symptom-list");
  await expect(timeline.getByTestId("symptom-card")).toHaveCount(1);
  await expect(timeline.getByTestId("symptom-card").first()).toContainText("Headache");
  await expect(timeline.getByTestId("symptom-card").first()).toContainText("Severity 2/5");
  await expect(timeline.getByTestId("symptom-card").first()).toContainText("45 minutes");
  await expect(timeline.getByTestId("symptom-card").first()).toContainText("Estimated 240 days");
  await expect(timeline.getByTestId("symptom-card").first()).toContainText(
    "Backdated first symptom",
  );

  await symptomForm.getByLabel("Symptom").fill("Nausea");
  await symptomForm.locator('input[name="occurred_at"]').fill("2026-07-21T10:00");
  await symptomForm.getByLabel("Severity (1-5)").fill("4");
  await symptomForm.getByRole("button", { name: "Save symptom" }).click();

  const cards = timeline.getByTestId("symptom-card");
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(0)).toContainText("Nausea");
  await expect(cards.nth(0)).toContainText("Severity 4/5");
  await expect(cards.nth(1)).toContainText("Headache");

  const metricForm = page.getByTestId("metric-form");
  await metricForm.locator('input[name="heart_rate_bpm"]').fill("70");
  await metricForm.getByRole("button", { name: "Save metric" }).click();
  await expect(page.getByTestId("metric-card")).toContainText("70 bpm");

  await page.getByTestId("person-form").getByLabel("Display name").fill("Family Person");
  await page.getByTestId("person-form").getByLabel("Relationship").selectOption("family");
  await page.getByRole("button", { name: "Create Person" }).click();
  await expect(page.getByTestId("person-card")).toHaveCount(2);

  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page.getByTestId("login-form")).toBeVisible();
  await page.getByTestId("login-form").getByLabel("Email").fill(email);
  await page.getByTestId("login-form").getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByTestId("session-email")).toHaveText(email);
  await expect(page.getByTestId("person-card")).toHaveCount(2);
});
