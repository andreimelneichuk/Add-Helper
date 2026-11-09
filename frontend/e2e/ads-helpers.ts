import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";

/**
 * Открывает первое работающее объявление и возвращает его заголовок.
 * Кнопки «Отключить» нет на карточке в списке: она на экране самого объявления.
 */
export async function openWorkingAd(page: Page): Promise<string> {
  const card = page.locator(".ann-card:not(.ann-card-off)").filter({ hasText: /работает/i }).first();
  await expect(card).toBeVisible();
  const title = (await card.locator(".ann-title").first().innerText()).trim();
  await card.click();
  await expect(page.getByRole("heading", { name: title }).first()).toBeVisible();
  return title;
}

/** С экрана объявления назад к списку. */
export async function backToAds(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Все объявления" }).click();
  await expect(page.locator(".ann-cards")).toBeVisible();
}
