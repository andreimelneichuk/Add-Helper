import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const notice = (page: Page) => page.locator(".ah-notice");
const nav = (page: Page, name: string) => page.locator(".ah-rail").getByRole("button", { name });

test.describe.configure({ mode: "serial" });

test("обзор показывает настоящие данные, а не прототип", async ({ page }) => {
  await page.goto("/?new");
  await expect(page.locator(".ah-brand")).toHaveText("Студия маникюра «Лак»");
  await expect(page.locator(".ah-proto")).toHaveCount(0);
  await expect(page.getByText("Салон «Пример»")).toHaveCount(0);
});

test("отключение объявления доходит до журнала и видно в истории", async ({ page }) => {
  await page.goto("/?new");
  await nav(page, "Реклама").click();
  // Карточки решений по новым объявлениям стоят выше и кнопки «Отключить» не имеют.
  const card = page.locator(".ads-card").filter({ has: page.getByRole("button", { name: /^Отключить/ }) }).first();
  const name = await card.locator(".ads-title").innerText();
  await card.getByRole("button", { name: /^Отключить/ }).click();
  await page.getByRole("button", { name: "Применить" }).click();
  await expect(notice(page)).toContainText(`Отключил «${name}»`);

  await page.reload();
  await nav(page, "Реклама").click();
  await page.getByRole("button", { name: /^Отключены/ }).click();
  await expect(page.locator(".ads-card-off .ads-title", { hasText: name }).first()).toBeVisible();

  await nav(page, "История").click();
  await expect(page.getByRole("heading", { name: `Отключил объявление: «${name}»` })).toBeVisible();
});

test("отмена возвращает объявление новой записью, исходная остаётся", async ({ page }) => {
  await page.goto("/?new");
  await nav(page, "История").click();
  const row = page.locator(".ah-hist-row", { hasText: "Отключил объявление" }).first();
  await row.getByRole("button", { name: "Отменить" }).click();
  await page.getByRole("button", { name: "Да, отменить" }).click();
  await expect(notice(page)).toContainText("Отменил");
  await expect(page.locator(".ah-hist-row", { hasText: "Включил объявление" }).first()).toBeVisible();
  await expect(page.locator(".ah-hist-row", { hasText: "Отключил объявление" }).first()).toBeVisible();
});

test("пауза из шапки останавливает показы и переживает перезагрузку", async ({ page }) => {
  await page.goto("/?new");
  await page.locator(".ah-bar").getByRole("button", { name: "Пауза" }).click();
  await expect(notice(page)).toContainText("Поставил на паузу");
  await page.reload();
  await expect(page.locator(".ah-status")).toHaveText("На паузе");
});
