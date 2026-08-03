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

test("unified Today view aggregates records and shows evidence-linked guidance", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource:")) {
      consoleErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => {
    consoleErrors.push(error.message);
  });

  const marker = Date.now();
  const email = `today-owner-${marker}@example.com`;
  await register(page, email, "Today Owner");

  await page.getByTestId("person-card").first().click();
  await expect(page.getByTestId("selected-person-pill")).toBeVisible();

  const todaySection = page.getByTestId("today-section");
  await expect(todaySection.getByTestId("today-latest-metric-empty")).toBeVisible();
  const initialAttention = todaySection.getByTestId("daily-attention-item");
  await expect(initialAttention).toHaveCount(1);
  await expect(initialAttention.first()).toHaveAttribute(
    "data-attention-kind",
    "insufficient_data",
  );

  const recentDate = new Date(Date.now() - 2 * 24 * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 16);
  const symptomForm = page.getByTestId("symptom-form");
  await symptomForm.getByLabel("Symptom").fill("Backdated headache");
  await symptomForm
    .locator('input[name="occurred_at"]')
    .fill(recentDate);
  await symptomForm.getByLabel("Severity (1-5)").fill("3");

  await symptomForm.getByRole("button", { name: "Save symptom" }).click();
  await expect(page.getByTestId("symptom-list").getByTestId("symptom-card")).toHaveCount(1);

  const metricForm = page.getByTestId("metric-form");
  await metricForm.locator('input[name="heart_rate_bpm"]').fill("70");
  await metricForm.getByRole("button", { name: "Save metric" }).click();
  await expect(page.getByTestId("metric-list").getByTestId("metric-card")).toHaveCount(1);

  const actionForm = page.getByTestId("action-form");
  await actionForm.getByLabel("Title").fill("Evening walk");
  await actionForm.getByRole("button", { name: "Create action" }).click();
  const actionList = page.getByTestId("action-list");
  await expect(actionList.getByTestId("action-card")).toHaveCount(1);
  const completedCard = actionList.getByTestId("action-card").filter({
    hasText: "Evening walk",
  });
  await completedCard.getByRole("button", { name: "Complete action" }).click();
  await expect(completedCard).toContainText("Status: done");

  await actionForm.getByLabel("Title").fill("Track blood pressure");
  await actionForm.getByRole("button", { name: "Create action" }).click();
  await expect(actionList.getByTestId("action-card")).toHaveCount(2);

  const outcomeForm = page.getByTestId("outcome-form");
  await expect(outcomeForm).toBeVisible();
  await outcomeForm.getByLabel("Note").fill("Felt noticeably better.");
  await outcomeForm.getByRole("button", { name: "Save outcome" }).click();

  await expect(todaySection.getByTestId("today-symptom-card")).toHaveCount(1);
  await expect(todaySection.getByTestId("today-symptom-card").first()).toContainText(
    "Backdated headache",
  );
  await expect(todaySection.getByTestId("today-action-card")).toHaveCount(2);
  const todayActionText = await todaySection.getByTestId("today-action-list").innerText();
  expect(todayActionText).toContain("done");
  expect(todayActionText).toContain("todo");
  await expect(todaySection.getByTestId("today-outcome-card")).toHaveCount(1);
  await expect(todaySection.getByTestId("today-outcome-card").first()).toContainText(
    "Felt noticeably better.",
  );
  await expect(todaySection.getByTestId("today-latest-metric")).toBeVisible();

  const attentionKinds = await todaySection
    .getByTestId("daily-attention-item")
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-attention-kind")));
  expect(attentionKinds).toContain("symptom_recently_reported");
  expect(attentionKinds).toContain("action_open_or_due");
  expect(attentionKinds).toContain("outcome_recorded");
  expect(attentionKinds).not.toContain("insufficient_data");

  const symptomItem = todaySection.locator(
    '[data-testid="daily-attention-item"][data-attention-kind="symptom_recently_reported"]',
  );
  await expect(symptomItem).toHaveAttribute("data-attention-confidence", /low|medium|high/);
  await expect(symptomItem.getByTestId("daily-attention-evidence-count")).toBeVisible();
  const evidenceCount = await symptomItem
    .getByTestId("daily-attention-evidence-count")
    .textContent();
  expect(Number(evidenceCount)).toBeGreaterThan(0);

  const firstSnapshot = await todaySection.innerText();
  await todaySection.getByTestId("today-refresh-button").click();
  await page.waitForTimeout(200);
  const secondSnapshot = await todaySection.innerText();
  const stripGeneratedAt = (text: string) =>
    text.replace(/Generated.*?·/s, "Generated ·");
  expect(stripGeneratedAt(secondSnapshot)).toBe(stripGeneratedAt(firstSnapshot));

  const storage = await page.evaluate(() => ({ ...localStorage }));
  const serializedStorage = JSON.stringify(storage);
  expect(Object.keys(storage).join(" ")).not.toMatch(/symptom|action|outcome|metric|assistant/i);
  expect(serializedStorage).not.toContain("Backdated headache");
  expect(serializedStorage).not.toContain("Felt noticeably better.");

  expect(consoleErrors).toEqual([]);
});
