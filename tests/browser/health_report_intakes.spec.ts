import { expect, test, type Page, type Route } from "@playwright/test";

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

function responseGate<T>(body: T): {
  requested: Promise<void>;
  release: () => void;
  fulfill: (route: Route) => Promise<void>;
} {
  let markRequested!: () => void;
  let releaseResponse!: () => void;
  const requested = new Promise<void>((resolve) => {
    markRequested = resolve;
  });
  const released = new Promise<void>((resolve) => {
    releaseResponse = resolve;
  });
  return {
    requested,
    release: () => releaseResponse(),
    fulfill: async (route) => {
      markRequested();
      await released;
      await route.fulfill({ json: body });
    },
  };
}

function intakeFixture(
  personId: string,
  intakeId: string,
  sourceName: string,
  status: "pending_review" | "confirmed",
  reportId: string | null = null,
) {
  return {
    id: intakeId,
    person_id: personId,
    source_filename: `${sourceName}.pdf`,
    source_name: sourceName,
    file_sha256: `${intakeId}-sha256`,
    media_type: "application/pdf",
    extraction_method: "digital_pdf",
    parser_metadata: { parser: "synthetic-browser" },
    status,
    pending_review: status === "pending_review",
    reported_at: "2026-09-01T00:00:00Z",
    error_message: null,
    report_id: reportId,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    confirmed_at: status === "confirmed" ? "2026-09-02T00:00:00Z" : null,
  };
}

function intakeDetailFixture(
  personId: string,
  intakeId: string,
  sourceName: string,
  status: "pending_review" | "confirmed",
  reportId: string | null = null,
) {
  return {
    ...intakeFixture(personId, intakeId, sourceName, status, reportId),
    observations: [
      {
        id: `${intakeId}-observation`,
        intake_id: intakeId,
        ordinal: 0,
        code: "GLUCOSE",
        display_name: "Glucose",
        value_numeric: 92,
        value_text: null,
        unit: "mg/dL",
        reference_range: "65-99",
        observed_at: "2026-09-01T00:00:00Z",
        parser_provenance: "digital_pdf_text",
        parser_confidence: null,
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
      },
    ],
  };
}

function healthReportFixture(
  personId: string,
  reportId: string,
  sourceName: string,
  observationName: string,
) {
  return {
    id: reportId,
    person_id: personId,
    schema_version: "healthy.health-report.v1",
    source_name: sourceName,
    reported_at: "2026-09-01T00:00:00Z",
    canonical_sha256: `${reportId}-sha256`,
    status: "confirmed",
    created_at: "2026-09-01T00:00:00Z",
    confirmed_at: "2026-09-02T00:00:00Z",
    observations: [
      {
        id: `${reportId}-observation`,
        report_id: reportId,
        person_id: personId,
        code: "GLUCOSE",
        display_name: observationName,
        value_numeric: 92,
        value_text: null,
        unit: "mg/dL",
        reference_range: "65-99",
        observed_at: "2026-09-01T00:00:00Z",
        created_at: "2026-09-01T00:00:00Z",
      },
    ],
  };
}

function routePath(route: Route): string {
  return new URL(route.request().url()).pathname;
}

function routePersonId(route: Route): string {
  return routePath(route).split("/")[4] ?? "";
}

function routeIntakeId(route: Route): string {
  return routePath(route).split("/")[6] ?? "";
}

async function prepareTwoReportPeople(page: Page): Promise<{
  primaryPersonId: string;
  secondaryPersonId: string;
}> {
  const marker = Date.now();
  await register(
    page,
    `report-isolation-${marker}@example.com`,
    "Primary Report Person",
  );

  const primaryPerson = page.getByTestId("person-card").first();
  await primaryPerson.click();
  const primaryPersonId = await primaryPerson.getAttribute("data-person-id");
  if (!primaryPersonId) {
    throw new Error("Primary synthetic Person id was not rendered.");
  }

  await page
    .getByTestId("person-form")
    .getByLabel("Display name")
    .fill("Secondary Report Person");
  await page
    .getByTestId("person-form")
    .getByLabel("Relationship")
    .selectOption("family");
  await page.getByRole("button", { name: "Create Person" }).click();

  const secondaryPerson = page.getByTestId("person-card").filter({
    hasText: "Secondary Report Person",
  });
  await expect(secondaryPerson).toBeVisible();
  const secondaryPersonId = await secondaryPerson.getAttribute("data-person-id");
  if (!secondaryPersonId) {
    throw new Error("Secondary synthetic Person id was not rendered.");
  }
  return { primaryPersonId, secondaryPersonId };
}

async function openReportsForPerson(page: Page, personId: string): Promise<void> {
  await page.goto(`/reports?person_id=${encodeURIComponent(personId)}`);
  await expect(page.getByTestId("reports-page")).toBeVisible();
  await expect(page.getByTestId("reports-person-select")).toHaveValue(personId);
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

test("ignores a late confirmed-report detail after switching Person", async ({
  page,
}) => {
  const { primaryPersonId, secondaryPersonId } =
    await prepareTwoReportPeople(page);
  const primaryIntake = intakeFixture(
    primaryPersonId,
    "intake-late-a",
    "Person A confirmed report",
    "confirmed",
    "report-late-a",
  );
  const secondaryIntake = intakeFixture(
    secondaryPersonId,
    "intake-late-b",
    "Person B confirmed report",
    "confirmed",
    "report-late-b",
  );
  const primaryDetail = intakeDetailFixture(
    primaryPersonId,
    "intake-late-a",
    "Person A confirmed report",
    "confirmed",
    "report-late-a",
  );
  const secondaryDetail = intakeDetailFixture(
    secondaryPersonId,
    "intake-late-b",
    "Person B confirmed report",
    "confirmed",
    "report-late-b",
  );
  const primaryReport = healthReportFixture(
    primaryPersonId,
    "report-late-a",
    "Person A confirmed report",
    "Person A glucose marker",
  );
  const secondaryReport = healthReportFixture(
    secondaryPersonId,
    "report-late-b",
    "Person B confirmed report",
    "Person B glucose marker",
  );
  const primaryReportGate = responseGate(primaryReport);

  await page.route("**/api/v1/persons/*/report-intakes", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      json:
        routePersonId(route) === primaryPersonId
          ? [primaryIntake]
          : [secondaryIntake],
    });
  });
  await page.route("**/api/v1/persons/*/report-intakes/*", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    const intakeId = routeIntakeId(route);
    if (intakeId === "intake-late-a") {
      await route.fulfill({ json: primaryDetail });
      return;
    }
    if (intakeId === "intake-late-b") {
      await route.fulfill({ json: secondaryDetail });
      return;
    }
    await route.fallback();
  });
  await page.route("**/api/v1/persons/*/reports/*", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    if (routePath(route).endsWith("/reports/report-late-a")) {
      await primaryReportGate.fulfill(route);
      return;
    }
    if (routePath(route).endsWith("/reports/report-late-b")) {
      await route.fulfill({ json: secondaryReport });
      return;
    }
    await route.fallback();
  });

  await openReportsForPerson(page, primaryPersonId);
  await primaryReportGate.requested;

  await page
    .getByTestId("reports-person-select")
    .selectOption(secondaryPersonId);
  const confirmedCard = page.getByTestId("confirmed-health-report");
  await expect(confirmedCard).toContainText("Person B confirmed report");
  await expect(confirmedCard).toContainText("Person B glucose marker");

  primaryReportGate.release();
  await expect(confirmedCard).toContainText("Person B glucose marker");
  await expect(confirmedCard).not.toContainText("Person A glucose marker");
});

test("ignores an upload response after switching Person", async ({ page }) => {
  const { primaryPersonId, secondaryPersonId } =
    await prepareTwoReportPeople(page);
  const uploadedIntake = intakeDetailFixture(
    primaryPersonId,
    "intake-upload-a",
    "Person A uploaded report",
    "pending_review",
  );
  const uploadGate = responseGate(uploadedIntake);
  let uploadRequests = 0;

  await page.route("**/api/v1/persons/*/report-intakes", async (route) => {
    const method = route.request().method();
    if (method === "GET") {
      await route.fulfill({ json: [] });
      return;
    }
    if (method === "POST" && routePersonId(route) === primaryPersonId) {
      uploadRequests += 1;
      await uploadGate.fulfill(route);
      return;
    }
    await route.fallback();
  });
  await page.route("**/api/v1/persons/*/report-intakes/*", async (route) => {
    if (
      route.request().method() === "GET" &&
      routeIntakeId(route) === "intake-upload-a"
    ) {
      await route.fulfill({ json: uploadedIntake });
      return;
    }
    await route.fallback();
  });

  await openReportsForPerson(page, primaryPersonId);
  await expect(page.getByTestId("report-intake-empty")).toBeVisible();
  await page.getByTestId("report-file-input").setInputFiles({
    name: "late-upload.pdf",
    mimeType: "application/pdf",
    buffer: syntheticPdf(["Source: Person A", "Glucose: 92 mg/dL"]),
  });
  await page.getByTestId("report-upload-button").click();
  await uploadGate.requested;

  await page
    .getByTestId("reports-person-select")
    .selectOption(secondaryPersonId);
  await expect(page.getByTestId("reports-person-select")).toHaveValue(
    secondaryPersonId,
  );
  await expect(page.getByTestId("report-intake-empty")).toBeVisible();

  uploadGate.release();
  await expect(page.getByTestId("report-intake-empty")).toBeVisible();
  await expect(page.getByTestId("report-intake-card")).toHaveCount(0);
  await expect(page.getByTestId("report-review-section")).toHaveCount(0);
  expect(uploadRequests).toBe(1);
});

test("ignores a late save response after switching intake", async ({ page }) => {
  const { primaryPersonId } = await prepareTwoReportPeople(page);
  const primaryA = intakeDetailFixture(
    primaryPersonId,
    "intake-save-a",
    "Person A save target",
    "pending_review",
  );
  const primaryB = intakeDetailFixture(
    primaryPersonId,
    "intake-save-b",
    "Person B save target",
    "pending_review",
  );
  const saveGate = responseGate(primaryA);
  let saveRequests = 0;

  await page.route("**/api/v1/persons/*/report-intakes", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      json:
        routePersonId(route) === primaryPersonId
          ? [primaryA, primaryB]
          : [],
    });
  });
  await page.route("**/api/v1/persons/*/report-intakes/*", async (route) => {
    const method = route.request().method();
    const intakeId = routeIntakeId(route);
    if (method === "GET" && intakeId === "intake-save-a") {
      await route.fulfill({ json: primaryA });
      return;
    }
    if (method === "GET" && intakeId === "intake-save-b") {
      await route.fulfill({ json: primaryB });
      return;
    }
    if (
      method === "PATCH" &&
      routePersonId(route) === primaryPersonId &&
      intakeId === "intake-save-a"
    ) {
      saveRequests += 1;
      await saveGate.fulfill(route);
      return;
    }
    await route.fallback();
  });

  await openReportsForPerson(page, primaryPersonId);
  await expect(page.getByTestId("report-source-name")).toHaveValue(
    "Person A save target",
  );
  await page.getByTestId("report-review-save").click();
  await saveGate.requested;

  const secondaryIntakeCard = page.locator(
    '[data-testid="report-intake-card"][data-intake-id="intake-save-b"]',
  );
  await secondaryIntakeCard.getByRole("button").click();
  await expect(page.getByTestId("report-source-name")).toHaveValue(
    "Person B save target",
  );
  await expect(secondaryIntakeCard.getByRole("button")).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  saveGate.release();
  await expect(page.getByTestId("report-source-name")).toHaveValue(
    "Person B save target",
  );
  await expect(secondaryIntakeCard.getByRole("button")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByTestId("report-intake-message")).toHaveCount(0);
  expect(saveRequests).toBe(1);
});

test("does not target an old review while a new Person intake loads", async ({
  page,
}) => {
  const { primaryPersonId, secondaryPersonId } =
    await prepareTwoReportPeople(page);
  const primaryIntake = intakeFixture(
    primaryPersonId,
    "intake-confirm-a",
    "Person A confirm target",
    "pending_review",
  );
  const primaryDetail = intakeDetailFixture(
    primaryPersonId,
    "intake-confirm-a",
    "Person A confirm target",
    "pending_review",
  );
  const confirmedDetail = intakeDetailFixture(
    primaryPersonId,
    "intake-confirm-a",
    "Person A confirmed stale response",
    "confirmed",
    "report-confirm-a",
  );
  const confirmGate = responseGate(confirmedDetail);
  const secondaryIntakesGate = responseGate([]);
  let confirmRequests = 0;

  await page.route("**/api/v1/persons/*/report-intakes", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    if (routePersonId(route) === secondaryPersonId) {
      await secondaryIntakesGate.fulfill(route);
      return;
    }
    await route.fulfill({ json: [primaryIntake] });
  });
  await page.route("**/api/v1/persons/*/report-intakes/*", async (route) => {
    if (
      route.request().method() === "GET" &&
      routeIntakeId(route) === "intake-confirm-a"
    ) {
      await route.fulfill({ json: primaryDetail });
      return;
    }
    await route.fallback();
  });
  await page.route(
    "**/api/v1/persons/*/report-intakes/*/confirm",
    async (route) => {
      if (
        route.request().method() === "POST" &&
        routePersonId(route) === primaryPersonId &&
        routeIntakeId(route) === "intake-confirm-a"
      ) {
        confirmRequests += 1;
        await confirmGate.fulfill(route);
        return;
      }
      await route.fallback();
    },
  );

  await openReportsForPerson(page, primaryPersonId);
  await expect(page.getByTestId("report-source-name")).toHaveValue(
    "Person A confirm target",
  );
  await page.getByTestId("report-review-confirm").click();
  await confirmGate.requested;

  await page
    .getByTestId("reports-person-select")
    .selectOption(secondaryPersonId);
  await secondaryIntakesGate.requested;
  await expect(page.getByTestId("report-review-section")).toHaveCount(0);
  await expect(page.getByTestId("report-review-confirm")).toHaveCount(0);

  confirmGate.release();
  await expect(page.getByTestId("report-review-section")).toHaveCount(0);
  await expect(page.getByTestId("confirmed-health-report")).toHaveCount(0);
  await expect(page.getByTestId("report-intake-card")).toHaveCount(0);

  secondaryIntakesGate.release();
  await expect(page.getByTestId("report-intake-empty")).toBeVisible();
  await expect(page.getByTestId("report-review-section")).toHaveCount(0);
  await expect(page.getByTestId("confirmed-health-report")).toHaveCount(0);
  expect(confirmRequests).toBe(1);
});
