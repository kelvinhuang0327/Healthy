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

test("Home has one report action and Today uses confirmed file reports", async ({
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

  const selectedPerson = page.getByTestId("person-card").first();
  await selectedPerson.click();
  await expect(page.getByTestId("selected-person-pill")).toBeVisible();

  const personId = await selectedPerson.getAttribute("data-person-id");
  expect(personId).not.toBeNull();
  const expectedReportsPath = `/reports?person_id=${encodeURIComponent(personId as string)}`;
  const reportsLink = page.getByTestId("reports-link");
  await expect(reportsLink).toHaveCount(1);
  await expect(reportsLink).toHaveAttribute("href", expectedReportsPath);
  await expect(page.getByTestId("report-import-form")).toHaveCount(0);
  await expect(page.getByTestId("report-list")).toHaveCount(0);
  await expect(page.getByTestId("confirm-report-button")).toHaveCount(0);
  await expect(page.getByTestId("confirm-detail-button")).toHaveCount(0);

  await reportsLink.click();
  await expect(page).toHaveURL(new RegExp(`${expectedReportsPath.replace("?", "\\?")}$`));
  await expect(page.getByTestId("reports-person-select")).toHaveValue(personId as string);

  await page.getByTestId("report-file-input").setInputFiles({
    name: "synthetic-today-report.pdf",
    mimeType: "application/pdf",
    buffer: syntheticPdf([
      "Source: Quest Diagnostics",
      `Report Date: ${new Date().toISOString().slice(0, 10)}`,
      "Glucose: 92 mg/dL (65-99)",
    ]),
  });
  await page.getByTestId("report-upload-button").click();

  const pendingCard = page.locator(
    '[data-testid="report-intake-card"][data-intake-status="pending_review"]',
  );
  await expect(pendingCard).toBeVisible();
  await expect(page.getByTestId("report-review-section")).toBeVisible();

  const todaySection = page.getByTestId("today-section");
  await page.getByRole("link", { name: "Back to Today" }).click();
  await expect(todaySection).toBeVisible();
  const pendingAttentionKinds = await todaySection
    .getByTestId("daily-attention-item")
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-attention-kind")));
  expect(pendingAttentionKinds).not.toContain("recent_report_imported");

  await page.getByTestId("reports-link").click();
  await expect(page.getByTestId("report-review-section")).toBeVisible();
  await page.getByTestId("report-candidate-value").first().fill("91.25");
  await page.getByTestId("report-source-name").fill("Corrected Browser Lab");
  await page.getByTestId("report-review-save").click();
  await expect(page.getByTestId("report-intake-message")).toContainText("saved");

  await page.reload();
  await expect(page.getByTestId("report-review-section")).toBeVisible();
  await expect(page.getByTestId("report-candidate-value").first()).toHaveValue("91.25");
  await expect(page.getByTestId("report-source-name")).toHaveValue("Corrected Browser Lab");
  await page.getByTestId("report-review-confirm").click();
  await expect(page.getByTestId("confirmed-health-report")).toBeVisible();
  await expect(page.getByTestId("confirmed-health-report")).toContainText("healthy.health-report.v1");

  await page.getByRole("link", { name: "Back to Today" }).click();
  await expect(todaySection).toBeVisible();

  const confirmedReportItem = todaySection.locator(
    '[data-testid="daily-attention-item"][data-attention-kind="recent_report_imported"]',
  );
  await expect(confirmedReportItem).toBeVisible();
  await expect(confirmedReportItem).toHaveAttribute("data-attention-confidence", "medium");
  await expect(todaySection.getByTestId("today-insight-card")).toContainText("Corrected Browser Lab");

  const storage = await page.evaluate(() => ({ ...localStorage }));
  const serializedStorage = JSON.stringify(storage);
  expect(serializedStorage).not.toContain("Corrected Browser Lab");
  expect(serializedStorage).not.toContain("GLUCOSE");

  expect(consoleErrors).toEqual([]);
});
