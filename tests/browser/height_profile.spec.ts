import { expect, test } from "@playwright/test";

const password = "Synthetic-Only-Password-42";

test("authenticate, update current Person height, and reload it", async ({
  page,
}) => {
  const marker = Date.now();
  await page.goto("/");
  await page.getByTestId("register-form").getByLabel("Email").fill(
    `height-${marker}@example.com`,
  );
  await page.getByTestId("register-form").getByLabel("Password").fill(password);
  await page
    .getByTestId("register-form")
    .getByLabel("Your display name")
    .fill("Height Browser Person");
  await page.getByRole("button", { name: "Register securely" }).click();

  const heightProfile = page.getByTestId("height-profile");
  await expect(heightProfile).toBeVisible();
  await expect(page.getByTestId("height-empty")).toHaveText(
    "No height recorded yet.",
  );
  await heightProfile.getByLabel("Height (cm)").fill("173.25");
  await heightProfile.getByRole("button", { name: "Save height" }).click();
  await expect(page.getByTestId("height-value")).toHaveText(
    "Current height: 173.25 cm",
  );

  await page.reload();
  await expect(page.getByTestId("height-value")).toHaveText(
    "Current height: 173.25 cm",
  );
  await page.getByTestId("history-link").click();
  await expect(page.getByTestId("history-page")).toBeVisible();
  await page.getByRole("link", { name: "Healthy" }).click();
  await expect(page.getByTestId("height-value")).toHaveText(
    "Current height: 173.25 cm",
  );
  await expect(page.getByText("Session active")).toBeVisible();
});
