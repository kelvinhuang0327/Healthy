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

async function localDateTimeForUtc(page: Page, instant: Date): Promise<string> {
  return page.evaluate((utcInstant) => {
    const utcDate = new Date(utcInstant);
    const localDate = new Date(
      utcDate.getTime() - utcDate.getTimezoneOffset() * 60_000,
    );
    return localDate.toISOString().slice(0, 16);
  }, instant.toISOString());
}

function syntheticPdf(lines: string[]): Buffer {
  const commands = lines.map((line, index) => {
    const escaped = line
      .replaceAll("\\", "\\\\")
      .replaceAll("(", "\\(")
      .replaceAll(")", "\\)");
    return `BT /F1 18 Tf 60 ${730 - index * 28} Td (${escaped}) Tj ET`;
  });
  const stream = Buffer.from(commands.join("\n"), "utf8");
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    `<< /Length ${stream.length} >>\nstream\n${stream.toString("utf8")}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  const chunks: string[] = ["%PDF-1.4\n"];
  const offsets = [0];
  for (let index = 0; index < objects.length; index += 1) {
    offsets.push(Buffer.byteLength(chunks.join(""), "utf8"));
    chunks.push(`${index + 1} 0 obj\n${objects[index]}\nendobj\n`);
  }
  const xref = Buffer.byteLength(chunks.join(""), "utf8");
  chunks.push(`xref\n0 ${objects.length + 1}\n`);
  chunks.push("0000000000 65535 f \n");
  for (const offset of offsets.slice(1)) {
    chunks.push(`${String(offset).padStart(10, "0")} 00000 n \n`);
  }
  chunks.push(
    `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`,
  );
  return Buffer.from(chunks.join(""), "utf8");
}

test("Health History shows mixed sources in order and filters by type", async ({
  page,
}) => {
  const marker = Date.now();
  await register(page, `history-owner-${marker}@example.com`, "History Owner");
  await page.getByTestId("person-card").first().click();
  await expect(page.getByTestId("selected-person-pill")).toBeVisible();

  const reportDate = new Date(Date.now() - 24 * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 10);
  const reportAt = new Date(`${reportDate}T00:00:00Z`);
  const metricTime = await localDateTimeForUtc(
    page,
    new Date(reportAt.getTime() - 48 * 60 * 60 * 1000),
  );
  const symptomTime = await localDateTimeForUtc(
    page,
    new Date(reportAt.getTime() - 24 * 60 * 60 * 1000),
  );
  const outcomeAt = new Date(reportAt.getTime() + 12 * 60 * 60 * 1000);
  const outcomeTime = await localDateTimeForUtc(page, outcomeAt);

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

  const actionForm = page.getByTestId("action-form");
  await actionForm.getByLabel("Title").fill("Evening walk");
  await actionForm.getByRole("button", { name: "Create action" }).click();
  const actionCard = page.getByTestId("action-list").getByTestId("action-card");
  await expect(actionCard).toHaveCount(1);
  await actionCard.getByRole("button", { name: "Complete action" }).click();
  await expect(actionCard).toHaveAttribute("data-action-status", "done");

  const outcomeNote = "After the evening walk, I felt more rested.";
  const outcomeForm = page.getByTestId("outcome-form");
  await expect(outcomeForm).toBeVisible();
  await outcomeForm.locator('input[name="observed_at"]').fill(outcomeTime);
  await outcomeForm.getByLabel("Note").fill(outcomeNote);
  await outcomeForm.getByRole("button", { name: "Save outcome" }).click();
  await expect(
    page.getByTestId("today-outcome-card").filter({ hasText: outcomeNote }),
  ).toHaveCount(1);

  await page.getByTestId("reports-link").click();
  await expect(page.getByTestId("reports-page")).toBeVisible();

  await page.getByTestId("report-file-input").setInputFiles({
    name: "pending-history.pdf",
    mimeType: "application/pdf",
    buffer: syntheticPdf([
      "Source: Pending History Lab",
      `Report Date: ${reportDate}`,
      "Pending history glucose: 101 mg/dL (65-99)",
    ]),
  });
  await page.getByTestId("report-upload-button").click();
  await expect(
    page
      .locator('[data-testid="report-intake-card"]')
      .filter({ hasText: "Pending History Lab" }),
  ).toHaveAttribute("data-intake-status", "pending_review");

  await page.getByTestId("report-file-input").setInputFiles({
    name: "confirmed-history.pdf",
    mimeType: "application/pdf",
    buffer: syntheticPdf([
      "Source: Confirmed History Lab",
      `Report Date: ${reportDate}`,
      "Confirmed history glucose: 95.5 mg/dL (65-99)",
    ]),
  });
  await page.getByTestId("report-upload-button").click();
  const confirmedIntake = page
    .locator('[data-testid="report-intake-card"]')
    .filter({ hasText: "Confirmed History Lab" });
  await expect(confirmedIntake).toHaveAttribute(
    "data-intake-status",
    "pending_review",
  );
  await page.getByTestId("report-review-save").click();
  await page.getByTestId("report-review-confirm").click();
  await expect(confirmedIntake).toHaveAttribute("data-intake-status", "confirmed");

  await page.getByRole("link", { name: "Back to Today" }).click();
  await page.getByTestId("history-link").click();
  await expect(page).toHaveURL(/\/history\?person_id=/);
  await expect(page.getByRole("heading", { name: "Health History" })).toBeVisible();

  const historyItems = page.getByTestId("history-item");
  await expect(historyItems).toHaveCount(4);
  await expect(historyItems.nth(0)).toHaveAttribute("data-history-kind", "action_outcome");
  await expect(historyItems.nth(0)).toContainText("Action outcome");
  await expect(historyItems.nth(0)).toContainText(outcomeNote);
  await expect(historyItems.nth(0)).toContainText("Action: Evening walk");
  const displayedOutcomeAt = await historyItems
    .nth(0)
    .locator("time")
    .getAttribute("datetime");
  expect(new Date(displayedOutcomeAt ?? "").toISOString()).toBe(outcomeAt.toISOString());
  await expect(historyItems.nth(1)).toHaveAttribute("data-history-kind", "report_observation");
  await expect(historyItems.nth(1)).toContainText("Confirmed history glucose");
  await expect(historyItems.nth(1)).toContainText("Confirmed History Lab");
  await expect(historyItems.nth(2)).toHaveAttribute("data-history-kind", "symptom");
  await expect(historyItems.nth(2)).toContainText("History headache");
  await expect(historyItems.nth(3)).toHaveAttribute("data-history-kind", "metric");
  await expect(historyItems.nth(3)).toContainText("72 bpm");
  await expect(page.getByText("Pending history glucose")).toHaveCount(0);

  await page.getByTestId("history-filter-action_outcome").click();
  await expect(historyItems).toHaveCount(1);
  await expect(historyItems.first()).toContainText(outcomeNote);
  await page.getByTestId("history-filter-report_observation").click();
  await expect(historyItems).toHaveCount(1);
  await expect(historyItems.first()).toContainText("Confirmed history glucose");
  await page.getByTestId("history-filter-all").click();
  await expect(historyItems).toHaveCount(4);
});

test("empty Health History shows an explicit empty state", async ({ page }) => {
  const marker = Date.now();
  await register(page, `empty-history-owner-${marker}@example.com`, "Empty History Owner");
  await page.getByTestId("history-link").click();
  await expect(page.getByTestId("history-empty")).toBeVisible();
  await expect(page.getByTestId("history-empty")).toContainText("No health history yet");
});

test("editing measurements and symptoms keeps Today, History, and People in sync", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const marker = Date.now();
  await register(page, `edit-owner-${marker}@example.com`, "Edit Owner");
  await page.getByTestId("person-card").first().click();

  const occurredAt = await localDateTimeForUtc(page, new Date(Date.now() - 30 * 60_000));
  const symptomForm = page.getByTestId("symptom-form");
  await symptomForm.getByLabel("Symptom").fill("Headache");
  await symptomForm.locator('input[name="occurred_at"]').fill(occurredAt);
  await symptomForm.getByLabel("Severity (1-5)").fill("2");
  await symptomForm.getByLabel("Note").fill("Initial note");
  await symptomForm.getByRole("button", { name: "Save symptom" }).click();
  const symptomCard = page.getByTestId("symptom-list").getByTestId("symptom-card");
  await expect(symptomCard).toHaveCount(1);

  const metricForm = page.getByTestId("metric-form");
  await metricForm.locator('input[name="heart_rate_bpm"]').fill("72");
  await metricForm.getByRole("button", { name: "Save metric" }).click();
  const metricCard = page.getByTestId("metric-list").getByTestId("metric-card");
  await expect(metricCard).toHaveCount(1);
  const metricId = await metricCard.getAttribute("data-metric-id");
  const symptomId = await symptomCard.getAttribute("data-symptom-id");

  await symptomCard.getByTestId("edit-symptom-button").click();
  const symptomEdit = page.getByTestId("symptom-edit-form");
  await symptomEdit.getByLabel("Symptom").fill("Migraine");
  await symptomEdit.getByLabel("Severity (1-5)").fill("4");
  await symptomEdit.getByLabel("Duration (minutes, optional)").fill("45");
  await symptomEdit.getByLabel("Note").fill("Corrected after review");
  await symptomEdit.getByRole("button", { name: "Save changes" }).click();
  await expect(symptomCard).toHaveCount(1);
  await expect(symptomCard.first()).toContainText("Migraine");
  await expect(symptomCard.first()).toContainText("Severity 4/5");
  await expect(symptomCard.first()).toContainText("45 minutes");

  await metricCard.getByTestId("edit-metric-button").click();
  const metricEdit = page.getByTestId("metric-edit-form");
  await metricEdit.locator('input[name="heart_rate_bpm"]').fill("75");
  await metricEdit.locator('input[name="blood_glucose_mg_dl"]').fill("95.55");
  await metricEdit.locator('input[name="note"]').fill("Corrected measurement");
  await metricEdit.getByRole("button", { name: "Save changes" }).click();
  await expect(metricCard).toHaveCount(1);
  await expect(metricCard.first()).toContainText("75 bpm");
  await expect(metricCard.first()).toContainText("95.55 mg/dL");
  await expect(metricCard.first()).toContainText("Corrected measurement");
  await expect(page.getByTestId("today-latest-metric")).toContainText("75 bpm");
  await expect(page.getByTestId("today-symptom-card")).toContainText("Migraine");

  await page.getByTestId("person-form").getByLabel("Display name").fill("Other Person");
  await page.getByRole("button", { name: "Create Person" }).click();
  await expect(page.getByTestId("person-card")).toHaveCount(2);
  await page.getByTestId("person-card").filter({ hasText: "Other Person" }).click();
  await expect(page.getByTestId("metric-list").getByTestId("metric-empty-state")).toBeVisible();
  await expect(page.getByTestId("symptom-list").getByTestId("symptom-empty-state")).toBeVisible();
  const ownerPerson = page.getByTestId("person-card").filter({ hasText: "Edit Owner" });
  await ownerPerson.focus();
  await ownerPerson.press("Space");
  await expect(page.getByTestId("metric-card")).toHaveCount(1);
  await expect(page.getByTestId("metric-card")).toContainText("75 bpm");
  await expect(page.getByTestId("symptom-card")).toHaveCount(1);
  await expect(page.getByTestId("symptom-card")).toContainText("Migraine");

  await page.getByTestId("history-link").click();
  const historyItems = page.getByTestId("history-item");
  await expect(historyItems).toHaveCount(2);
  await expect(historyItems.filter({ hasText: "Corrected measurement" })).toHaveCount(1);
  await expect(historyItems.filter({ hasText: "Migraine" })).toHaveCount(1);
  await expect(
    page.locator(`[data-testid="history-item"][data-source-id="${metricId}"]`),
  ).toHaveCount(1);
  await expect(
    page.locator(`[data-testid="history-item"][data-source-id="${symptomId}"]`),
  ).toHaveCount(1);

  const viewportWidths = await page.evaluate(() => ({
    viewport: window.innerWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(viewportWidths.content).toBeLessThanOrEqual(viewportWidths.viewport);

  await page.goto("/");
  await expect(page.getByTestId("session-email")).toContainText(`edit-owner-${marker}`);
  await expect(page.getByTestId("metric-card")).toHaveCount(1);
  await expect(page.getByTestId("metric-card")).toContainText("75 bpm");
  await expect(page.getByTestId("symptom-card")).toHaveCount(1);
  await expect(page.getByTestId("symptom-card")).toContainText("Migraine");
});
