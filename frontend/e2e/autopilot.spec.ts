// Автопилот: положение и расписание в настройках, плашка в шапке, остановка, чат, экономика.
// Режим «Сам» на этом стенде закрыт: AUTOPILOT_AUTO_ALLOWED не включён.
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const nav = (page: Page, name: string) => page.locator(".ah-rail").getByRole("button", { name });
const badge = (page: Page) => page.locator(".ah-bar-autopilot");

test.describe.configure({ mode: "serial" });

test("настройки автопилота: положение и расписание переживают перезагрузку, «Сам» закрыт", async ({ page }) => {
  await page.goto("/");
  await nav(page, "Настройки").click();
  const cards = page.getByRole("radiogroup", { name: "Положение автопилота" });
  await expect(cards.getByRole("radio", { name: /Предлагает/ })).toHaveAttribute("aria-checked", "true");
  await expect(cards.getByRole("radio", { name: /Действует сам/ })).toBeDisabled();

  await cards.getByRole("radio", { name: /Только следит/ }).click();
  await page.getByLabel("Как часто разбирать").selectOption("weekly");
  await expect(cards.getByRole("radio", { name: /Только следит/ })).toHaveAttribute("aria-checked", "true");
  await expect(badge(page)).toContainText("только следит");

  await page.reload();
  await nav(page, "Настройки").click();
  await expect(page.getByRole("radiogroup", { name: "Положение автопилота" }).getByRole("radio", { name: /Только следит/ })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByLabel("Как часто разбирать")).toHaveValue("weekly");
  await expect(page.getByRole("list", { name: "Что менялось в настройках" })).toContainText("Только следит");
  // Вернуть как было, чтобы остальные сценарии шли с настройками по умолчанию.
  await page.getByRole("radio", { name: /Предлагает/ }).click();
  await page.getByLabel("Как часто разбирать").selectOption("daily");
});

test("остановка автопилота просит подтверждения и снимается кнопкой", async ({ page }) => {
  await page.goto("/");
  await nav(page, "Настройки").click();
  await page.getByRole("button", { name: "Остановить автопилот" }).click();
  await expect(page.getByText(/Уже идущая реклама и её лимиты не изменятся/)).toBeVisible();
  await page.getByRole("button", { name: "Да, остановить" }).click();
  await expect(page.getByText(/Автопилот остановлен/).first()).toBeVisible();
  await expect(badge(page)).toContainText("остановлен");
  await page.getByRole("button", { name: "Снова включить автопилот" }).click();
  await expect(badge(page)).toContainText("предлагает");
});

test("чат: «выключи автопилот» спрашивает, потом выключает; справка без вопроса", async ({ page }) => {
  await page.goto("/");
  await nav(page, "Чат с ИИ").click();
  const input = page.locator("#ah-chat-input");
  await input.fill("что сделал автопилот");
  await page.locator(".ah-chat-send").click();
  await expect(page.locator(".ah-chat-msg-ai").last()).toContainText(/Автопилот включён/);

  await input.fill("выключи автопилот");
  await page.locator(".ah-chat-send").click();
  await expect(page.locator(".ah-chat-msg-ai").last()).toContainText(/остановить автопилот/);
  await page.locator(".ah-chat-chips").getByRole("button", { name: "Применить" }).click();
  await expect(page.locator(".ah-chat-msg-ai").last()).toContainText(/Остановил автопилот/);
  await expect(badge(page)).toContainText("остановлен");

  await input.fill("включи автопилот");
  await page.locator(".ah-chat-send").click();
  await page.locator(".ah-chat-chips").getByRole("button", { name: "Применить" }).click();
  await expect(badge(page)).toContainText("предлагает");
});

test("экономика: ориентир, подтверждение «Это мои цифры» и цель в рублях", async ({ page }) => {
  await page.goto("/");
  await nav(page, "Настройки").click();
  const block = page.getByRole("region", { name: "Сколько стоит клиент" });
  await expect(block).toContainText("ориентир");
  await expect(block).toContainText(/Можно платить за обращение до \d+ ₽/);
  await block.getByLabel("Прибыль с чека, %").fill("40");
  await block.getByRole("button", { name: "Это мои цифры" }).click();
  await expect(block).toContainText("Цифры подтверждены вами");

  await page.reload();
  await nav(page, "Настройки").click();
  await expect(page.getByRole("region", { name: "Сколько стоит клиент" }).getByLabel("Прибыль с чека, %")).toHaveValue("40");
  await block.getByLabel("Прибыль с чека, %").fill("0");
  await block.getByRole("button", { name: "Сохранить цифры" }).click();
  await expect(block.getByRole("alert")).toContainText("прибыль с чека");
});

test("на телефоне 360 px настройки автопилота и экономики не вылезают за экран", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await page.goto("/");
  await page.locator(".ah-nav").getByRole("button", { name: "Настройки" }).click();
  await expect(page.getByRole("radiogroup", { name: "Положение автопилота" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Сколько стоит клиент" })).toBeVisible();
  const overflow = await page.evaluate(() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
