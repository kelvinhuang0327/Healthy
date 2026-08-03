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

test("JSON report import, review confirmation flow, and Today integration", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (
      message.type() === "error" &&
      !message.text().startsWith("Failed to load resource:")
    ) {
      consoleErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => {
    consoleErrors.push(error.message);
  });

  const marker = Date.now();
  const email = `report-owner-${marker}@example.com`;
  await register(page, email, "Report Owner");

  await page.getByTestId("person-card").first().click();
  await expect(page.getByTestId("selected-person-pill")).toBeVisible();

  const reportPayload = JSON.stringify({
    schema_version: "healthy.health-report.v1",
    source_name: "Quest Diagnostics",
    reported_at: new Date().toISOString(),
    observations: [
      {
        code: "GLUCOSE",
        display_name: "Glucose Level",
        value_numeric: 92.0,
        unit: "mg/dL",
        reference_range: "65-99",
      },
    ],
  });

  const importForm = page.getByTestId("report-import-form");
  await importForm.locator('textarea[name="report_json"]').fill(reportPayload);
  await importForm.getByRole("button", { name: "Import structured report" }).click();

  const reportList = page.getByTestId("report-list");
  await expect(reportList.getByTestId("report-card")).toHaveCount(1);

  const reportCard = reportList.getByTestId("report-card").first();
  await expect(reportCard).toHaveAttribute("data-report-status", "pending");
  await expect(reportCard).toContainText("Quest Diagnostics");

  const todaySection = page.getByTestId("today-section");
  const pendingAttentionKinds = await todaySection
    .getByTestId("daily-attention-item")
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-attention-kind")));
  expect(pendingAttentionKinds).not.toContain("recent_report_imported");

  await reportCard.getByTestId("confirm-report-button").click();
  await expect(reportCard).toHaveAttribute("data-report-status", "confirmed");

  const confirmedReportItem = todaySection.locator(
    '[data-testid="daily-attention-item"][data-attention-kind="recent_report_imported"]',
  );
  await expect(confirmedReportItem).toBeVisible();
  await expect(confirmedReportItem).toHaveAttribute("data-attention-confidence", "medium");


  const storage = await page.evaluate(() => ({ ...localStorage }));
  const serializedStorage = JSON.stringify(storage);
  expect(serializedStorage).not.toContain("Quest Diagnostics");
  expect(serializedStorage).not.toContain("GLUCOSE");

  expect(consoleErrors).toEqual([]);
});
