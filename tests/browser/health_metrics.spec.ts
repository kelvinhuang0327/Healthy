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

  const form = page.getByTestId("metric-form");
  await form.locator('input[name="systolic_bp_mm_hg"]').fill("120");
  await form.locator('input[name="diastolic_bp_mm_hg"]').fill("80");
  await form.locator('input[name="heart_rate_bpm"]').fill("72");
  await form.locator('input[name="weight_kg"]').fill("70.25");
  await form.locator('input[name="blood_glucose_mg_dl"]').fill("95.5");
  await form.locator('input[name="note"]').fill("After breakfast");
  await form.getByRole("button", { name: "Save metric" }).click();

  const metricList = page.getByTestId("metric-list");
  await expect(metricList.getByTestId("metric-card")).toHaveCount(1);
  const firstCard = metricList.getByTestId("metric-card").first();
  await expect(firstCard).toContainText("120/80 mmHg");
  await expect(firstCard).toContainText("72 bpm");
  await expect(firstCard).toContainText("70.25 kg");
  await expect(firstCard).toContainText("95.5 mg/dL");
  await expect(firstCard).toContainText("After breakfast");

  await form.locator('input[name="heart_rate_bpm"]').fill("65");
  await form.getByRole("button", { name: "Save metric" }).click();
  await expect(metricList.getByTestId("metric-card")).toHaveCount(2);
  const cards = metricList.getByTestId("metric-card");
  await expect(cards.nth(0)).toContainText("65 bpm");
  await expect(cards.nth(1)).toContainText("72 bpm");

  const foreignPersonStatus = await page.evaluate(async (id) => {
    const response = await fetch(`/api/v1/persons/${id}/metrics`, {
      credentials: "same-origin",
    });
    return response.status;
  }, personId);
  expect(foreignPersonStatus).toBe(200);

  expect(consoleErrors).toEqual([]);
});
