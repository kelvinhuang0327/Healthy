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

test("upload, review, persistence, and explicit canonical confirmation", async ({
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
  await register(
    page,
    `report-file-owner-${marker}@example.com`,
    "Report File Owner",
  );
  await page.getByTestId("person-card").first().click();
  await expect(page.getByTestId("reports-link")).toBeVisible();
  await page.getByTestId("reports-link").click();
  await expect(page.getByTestId("reports-page")).toBeVisible();

  await page.getByTestId("report-file-input").setInputFiles({
    name: "synthetic-lab.pdf",
    mimeType: "application/pdf",
    buffer: syntheticPdf([
      "Source: Synthetic Browser Lab",
      "Report Date: 2026-09-01",
      "Glucose: 92 mg/dL (65-99)",
    ]),
  });
  await page.getByTestId("report-upload-button").click();

  const pendingCard = page.locator(
    '[data-testid="report-intake-card"][data-intake-status="pending_review"]',
  );
  await expect(pendingCard).toBeVisible();
  await expect(page.getByTestId("report-review-section")).toBeVisible();
  await expect(page.getByTestId("report-review-warning")).toContainText(
    "draft",
  );
  await expect(
    page.getByTestId("report-candidate-reference").first(),
  ).toHaveValue("65-99");

  await page.getByTestId("report-candidate-value").first().fill("91.25");
  await page.getByTestId("report-source-name").fill("Corrected Browser Lab");
  await page.getByTestId("report-review-save").click();
  await expect(page.getByTestId("report-intake-message")).toContainText(
    "saved",
  );

  await page.reload();
  await expect(page.getByTestId("report-review-section")).toBeVisible();
  await expect(page.getByTestId("report-candidate-value").first()).toHaveValue(
    "91.25",
  );
  await expect(page.getByTestId("report-source-name")).toHaveValue(
    "Corrected Browser Lab",
  );
  await expect(page.getByTestId("report-review-confirm")).toBeEnabled();

  await page.getByTestId("report-review-confirm").click();
  await expect(page.getByTestId("confirmed-health-report")).toBeVisible();
  await expect(page.getByTestId("confirmed-health-report")).toContainText(
    "healthy.health-report.v1",
  );
  await expect(page.getByTestId("confirmed-report-history-link")).toBeVisible();

  await page.reload();
  await expect(page.getByTestId("confirmed-health-report")).toBeVisible();
  await expect(page.getByTestId("confirmed-health-report")).toContainText(
    "Corrected Browser Lab",
  );
  await expect(page.getByTestId("confirmed-health-report")).toContainText(
    "91.25",
  );
  await expect(page.getByTestId("confirmed-health-report")).toContainText(
    "Reference 65-99",
  );
  expect(consoleErrors).toEqual([]);
});

test("report navigation stays isolated to the selected Person", async ({
  page,
}) => {
  const marker = Date.now();
  await register(
    page,
    `report-person-scope-${marker}@example.com`,
    "Primary Report Person",
  );

  const primaryPerson = page.getByTestId("person-card").first();
  await primaryPerson.click();
  const primaryPersonId = await primaryPerson.getAttribute("data-person-id");
  expect(primaryPersonId).not.toBeNull();

  await page.getByTestId("person-form").getByLabel("Display name").fill("Other Report Person");
  await page
    .getByTestId("person-form")
    .getByLabel("Relationship")
    .selectOption("family");
  await page.getByRole("button", { name: "Create Person" }).click();

  const otherPerson = page.getByTestId("person-card").filter({
    hasText: "Other Report Person",
  });
  await expect(otherPerson).toBeVisible();
  await otherPerson.click();
  const otherPersonId = await otherPerson.getAttribute("data-person-id");
  expect(otherPersonId).not.toBeNull();

  const reportsLink = page.getByTestId("reports-link");
  const expectedReportsPath = `/reports?person_id=${encodeURIComponent(otherPersonId as string)}`;
  await expect(reportsLink).toHaveAttribute("href", expectedReportsPath);
  await reportsLink.click();
  await expect(page).toHaveURL(new RegExp(`${expectedReportsPath.replace("?", "\\?")}$`));
  await expect(page.getByTestId("reports-person-select")).toHaveValue(otherPersonId as string);
  await expect(page.getByTestId("report-intake-empty")).toBeVisible();

  await page.getByTestId("report-file-input").setInputFiles({
    name: "other-person-report.pdf",
    mimeType: "application/pdf",
    buffer: syntheticPdf([
      "Source: Isolated Report Lab",
      `Report Date: ${new Date().toISOString().slice(0, 10)}`,
      "Glucose: 88 mg/dL (65-99)",
    ]),
  });
  await page.getByTestId("report-upload-button").click();
  await expect(
    page.locator('[data-testid="report-intake-card"][data-intake-status="pending_review"]'),
  ).toBeVisible();
  await page.getByTestId("report-review-save").click();
  await page.getByTestId("report-review-confirm").click();
  await expect(page.getByTestId("confirmed-health-report")).toBeVisible();

  await page.getByTestId("reports-person-select").selectOption(primaryPersonId as string);
  await expect(page.getByTestId("reports-person-select")).toHaveValue(primaryPersonId as string);
  await expect(page.getByTestId("report-intake-empty")).toBeVisible();
  await expect(page.getByTestId("confirmed-health-report")).toHaveCount(0);

  await page.getByTestId("reports-person-select").selectOption(otherPersonId as string);
  await expect(page.getByTestId("reports-person-select")).toHaveValue(otherPersonId as string);
  await expect(page.getByTestId("confirmed-health-report")).toBeVisible();
});
