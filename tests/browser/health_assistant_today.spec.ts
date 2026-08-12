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
  const recentSymptomTimestamp = new Date(Date.now() - 24 * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 16);
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
  await expect(todaySection.getByTestId("today-risk-alert-count")).toHaveText(
    "Active alerts: 0",
  );
  await expect(todaySection.getByTestId("today-risk-alerts-empty")).toBeVisible();
  await expect(todaySection.getByTestId("today-action-recommendations-empty")).toBeVisible();
  await expect(todaySection.getByTestId("today-risk-alerts-disclaimer")).toContainText(
    "not a diagnosis",
  );

  const symptomForm = page.getByTestId("symptom-form");
  await symptomForm.getByLabel("Symptom").fill("Backdated headache");
  await symptomForm
    .locator('input[name="occurred_at"]')
    .fill(recentSymptomTimestamp);
  await symptomForm.getByLabel("Severity (1-5)").fill("3");
  await symptomForm.getByRole("button", { name: "Save symptom" }).click();
  await expect(page.getByTestId("symptom-list").getByTestId("symptom-card")).toHaveCount(1);

  await symptomForm.getByLabel("Symptom").fill("Backdated headache");
  await symptomForm
    .locator('input[name="occurred_at"]')
    .fill(new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString().slice(0, 16));
  await symptomForm.getByLabel("Severity (1-5)").fill("2");
  await symptomForm.getByRole("button", { name: "Save symptom" }).click();
  await expect(page.getByTestId("symptom-list").getByTestId("symptom-card")).toHaveCount(2);

  const metricForm = page.getByTestId("metric-form");
  await metricForm.locator('input[name="weight_kg"]').fill("90");
  await metricForm.locator('input[name="heart_rate_bpm"]').fill("70");
  await metricForm.getByRole("button", { name: "Save metric" }).click();
  await expect(page.getByTestId("metric-list").getByTestId("metric-card")).toHaveCount(1);
  await expect(todaySection.getByTestId("today-risk-alerts-empty")).toBeVisible();

  const heightProfile = page.getByTestId("height-profile");
  await heightProfile.getByLabel("Height (cm)").fill("170");
  await heightProfile.getByRole("button", { name: "Save height" }).click();
  await expect(page.getByTestId("height-value")).toHaveText("Current height: 170 cm");
  const riskAlerts = todaySection.getByTestId("today-risk-alert-card");
  await expect(riskAlerts).toHaveCount(1);
  await expect(riskAlerts.first()).toContainText("BMI_OBESE");
  await expect(riskAlerts.first()).toContainText("Severity: high");
  await expect(riskAlerts.first()).toContainText("Evidence: health_metric");
  const actionRecommendations = todaySection.getByTestId(
    "today-action-recommendation-card",
  );
  await expect(actionRecommendations).toHaveCount(1);
  await expect(actionRecommendations.first()).toContainText("BMI signal");
  await expect(actionRecommendations.first()).toContainText(
    "Review the source record",
  );
  await expect(actionRecommendations.first()).toContainText("Evidence: health_metric");
  await expect(actionRecommendations.first()).toContainText("not a diagnosis");

  await metricForm.locator('input[name="systolic_bp_mm_hg"]').fill("145");
  await metricForm.locator('input[name="diastolic_bp_mm_hg"]').fill("95");
  await metricForm.locator('input[name="heart_rate_bpm"]').fill("72");
  await metricForm.getByRole("button", { name: "Save metric" }).click();
  await expect(page.getByTestId("metric-list").getByTestId("metric-card")).toHaveCount(2);
  await expect(riskAlerts).toHaveCount(2);
  await expect(riskAlerts.filter({ hasText: "BP_HIGH" })).toContainText(
    "Evidence: health_metric",
  );
  await expect(actionRecommendations).toHaveCount(2);
  await expect(actionRecommendations.filter({ hasText: "Blood pressure signal" })).toContainText(
    "Source Risk Alert: BP_HIGH",
  );

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

  await expect(todaySection.getByTestId("today-symptom-card")).toHaveCount(2);
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

  const reportForm = page.getByTestId("report-import-form");
  const reportTimestamp = new Date().toISOString();
  await reportForm
    .getByLabel(/JSON report/)
    .fill(
      JSON.stringify({
        schema_version: "healthy.health-report.v1",
        source_name: "Pending Insights Lab",
        reported_at: reportTimestamp,
        observations: [
          {
            code: "PENDING_INSIGHTS_GLUCOSE",
            display_name: "Pending insights glucose",
            value_numeric: 101,
            unit: "mg/dL",
            observed_at: reportTimestamp,
          },
        ],
      }),
    );
  await reportForm.getByRole("button", { name: "Import structured report" }).click();
  const pendingReport = page
    .getByTestId("report-card")
    .filter({ hasText: "Pending Insights Lab" });
  await expect(pendingReport).toHaveAttribute("data-report-status", "pending");
  await expect(todaySection).not.toContainText("Pending insights glucose");

  await pendingReport.getByTestId("confirm-report-button").click();
  await expect(pendingReport).toHaveAttribute("data-report-status", "confirmed");
  await expect(todaySection.getByTestId("today-insight-card")).toHaveCount(3);
  await expect(
    todaySection
      .getByTestId("today-insight-card")
      .filter({ hasText: "Heart rate changed from 70 bpm to 72 bpm." }),
  ).toBeVisible();
  await expect(
    todaySection
      .getByTestId("today-insight-card")
      .filter({ hasText: "Backdated headache appears in 2 recorded symptom entries." }),
  ).toBeVisible();
  const confirmedInsight = todaySection
    .getByTestId("today-insight-card")
    .filter({ hasText: "Pending insights glucose" });
  await expect(confirmedInsight).toContainText("Source: Pending Insights Lab");
  await expect(confirmedInsight.getByRole("link", { name: "View evidence in Health History" })).toBeVisible();

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

  await confirmedInsight.getByRole("link", { name: "View evidence in Health History" }).click();
  await expect(page).toHaveURL(/\/history\?person_id=/);
});
