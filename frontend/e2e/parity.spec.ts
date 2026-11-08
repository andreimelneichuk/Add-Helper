// Функции сервиса, которые новый интерфейс перенял у прежнего кабинета: от кнопки до базы и обратно.
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const notice = (page: Page) => page.locator(".ah-notice");
const nav = (page: Page, name: string) => page.locator(".ah-rail").getByRole("button", { name });
const settings = (page: Page) => page.locator(".ah-rail").getByRole("button", { name: "Настройки" });

test.describe.configure({ mode: "serial" });

test("правка профиля сохраняется в базе", async ({ page }) => {
  // Цену наращивания цитирует только приостановленное объявление: ждущие решения правка не трогает.
  await page.goto("/");
  await settings(page).click();
  const price = page.getByLabel("Цена услуги 3, ₽");
  await expect(price).toHaveValue("2800");
  await price.fill("2900");
  await page.getByRole("button", { name: "Сохранить", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Сохранил" })).toBeVisible();

  await page.reload();
  await settings(page).click();
  await expect(page.getByLabel("Цена услуги 3, ₽")).toHaveValue("2900");
});

test("объявление, ждущее владельца, запускается и уходит в очередь", async ({ page }) => {
  await page.request.post("http://localhost:8011/api/stand/creatives/restore-needs-owner");
  await page.goto("/");
  await nav(page, "Реклама").click();
  const decide = page.getByRole("region", { name: "Нужно ваше решение" });
  const card = decide.getByRole("article", { name: "Решение по «Педикюр в студии «Лак»»" });
  await card.getByRole("button", { name: "Запустить" }).click();
  await expect(notice(page)).toContainText("Разрешил запуск «Педикюр в студии «Лак»»");

  await page.reload();
  await nav(page, "Реклама").click();
  const queue = page.getByRole("region", { name: "Готовлю к запуску" });
  await expect(queue.getByRole("listitem").filter({ hasText: "Педикюр в студии «Лак»" })).toContainText("уйдёт в Яндекс первым");
  await expect(page.getByRole("article", { name: "Решение по «Педикюр в студии «Лак»»" })).toHaveCount(0);
});

test("отчёт направления и свой период берут цифры у сервиса", async ({ page }) => {
  await page.goto("/");
  await nav(page, "Статистика").click();
  await page.getByRole("button", { name: "Отчёт по направлению «Маникюр — поиск»" }).click();
  await expect(page.getByRole("heading", { name: "Маникюр — поиск" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Объявления направления" }).getByRole("row")).not.toHaveCount(1);
  await page.getByRole("button", { name: "Назад к статистике" }).click();

  await page.getByRole("button", { name: "Свой период" }).click();
  await page.getByRole("form", { name: "Свой период" }).getByRole("button", { name: "Показать" }).click();
  await expect(page.getByRole("button", { name: / – / })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText(/^Обращения$/).locator("xpath=following-sibling::dd")).not.toHaveText("");
});

test("подключение кабинета: отказ в Яндексе виден владельцу, ничего не изменилось", async ({ page }) => {
  await page.goto("/");
  await settings(page).click();
  const account = page.getByRole("region", { name: "Кабинет Яндекса" });
  // Подставной Яндекс ID стенда: владелец отказывает на его странице, сервис возвращает его домой.
  await page.request.post("http://localhost:8011/stand-yandex/deny");
  await account.getByRole("button", { name: /подключить/i }).click();
  await expect(notice(page)).toContainText("Вы отменили подключение в Яндексе");
  await expect(page.getByRole("heading", { name: "Кабинет Яндекса" })).toBeVisible();
  await expect(page).toHaveURL(/:\d+\/$/);
});
