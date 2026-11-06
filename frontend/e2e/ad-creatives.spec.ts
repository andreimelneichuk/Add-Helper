import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const nav = (page: Page, name: string) => page.locator(".ah-rail").getByRole("button", { name });
const notice = (page: Page) => page.locator(".ah-notice");

const TEST_IMAGE_PATH = fileURLToPath(new URL("../../tests/platforms/direct/recorded/probe_image.jpg", import.meta.url));

test.describe.configure({ mode: "serial" });

test.describe("Интерактивная переделка объявлений и работа с фото (ad-creatives.spec.ts)", () => {
  test("1. Прототип (/?mock): экран объявления, чипы переделки, своё фото с чекбоксом согласия и возврат", async ({ page }) => {
    await page.goto("/?mock");

    // 1. Переход в раздел «Реклама»
    await nav(page, "Реклама").click();
    await expect(page.locator(".ads-card").first()).toBeVisible();

    // 2. Открытие карточки первого объявления
    const firstCard = page.locator(".ads-card").first();
    const adTitle = await firstCard.locator(".ads-title").innerText();
    await firstCard.getByRole("button", { name: /^Открыть/ }).click();

    // 3. Детальный экран объявления .ad-screen
    const screen = page.locator(".ad-screen");
    await expect(screen).toBeVisible();
    await expect(screen.locator(".ad-name")).toBeVisible();
    await expect(screen.locator(".ad-preview .ads-title")).toContainText(adTitle);
    await expect(screen.locator(".ad-back")).toBeVisible();
    await expect(screen.locator(".ad-preview")).toBeVisible();
    await expect(screen.locator(".ad-facts")).toBeVisible();

    // 4. Открытие формы переделки
    await screen.getByRole("button", { name: "Переделать" }).click();
    const compose = screen.locator(".ad-compose");
    await expect(compose).toBeVisible();

    // 5. Проверка чипов вариантов
    const chipShorter = compose.getByRole("button", { name: "Короче" });
    await expect(chipShorter).toBeVisible();
    await chipShorter.click();
    await expect(compose.locator(".ad-compare")).toBeVisible();
    await expect(compose.getByText("Новый вариант")).toBeVisible();

    const chipAccent = compose.getByRole("button", { name: "Другой акцент" });
    await chipAccent.click();
    await expect(chipAccent).toHaveAttribute("aria-pressed", "true");

    // 6. Проверка ввода своими словами
    const customInput = compose.getByPlaceholder("Сделайте упор на запись сегодня");
    await customInput.fill("Сделайте акцент на онлайн-запись");
    await compose.getByRole("button", { name: "Сделать" }).click();
    await expect(compose.locator(".ad-compare")).toBeVisible();

    // 7. Загрузка фото и валидация обязательного согласия
    const fileInput = compose.locator("#ad-photo");
    await fileInput.setInputFiles(TEST_IMAGE_PATH);

    // Появилось имя файла и чекбокс согласия
    await expect(compose.locator(".ad-compose-name")).toBeVisible();
    const consentLabel = compose.locator(".ad-compose-consent");
    await expect(consentLabel).toBeVisible();
    const consentCheckbox = consentLabel.locator("input[type='checkbox']");
    await expect(consentCheckbox).not.toBeChecked();

    // Кнопка «Поставить в показы» ЗАБЛОКИРОВАНА без согласия
    const applyBtn = compose.getByRole("button", { name: "Поставить в показы" });
    await expect(applyBtn).toBeDisabled();

    // Установка согласия делает кнопку активной
    await consentCheckbox.check();
    await expect(applyBtn).toBeEnabled();

    // 8. Клик «Отмена» закрывает форму и возвращает к объявлению
    await compose.getByRole("button", { name: "Отмена" }).click();
    await expect(compose).toHaveCount(0);
    await expect(screen.locator(".ad-preview")).toBeVisible();

    // 9. Кнопка «Все объявления» возвращает в общий список
    await screen.locator(".ad-back").click();
    await expect(page.locator(".ad-screen")).toHaveCount(0);
    await expect(page.locator(".ads-card").first()).toBeVisible();
  });

  test("2. Живой кабинет: переделка объявления через сервис, валидация фактов и постановка в очередь", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator(".ah-brand")).toHaveText("Студия маникюра «Лак»");

    await nav(page, "Реклама").click();

    // Находим карточку живого объявления и открываем детальный экран
    const adCard = page.locator(".ads-card").filter({ has: page.getByRole("button", { name: /^Открыть/ }) }).first();
    await adCard.getByRole("button", { name: /^Открыть/ }).click();

    const screen = page.locator(".ad-screen");
    await expect(screen).toBeVisible();

    // Нажимаем «Переделать»
    await screen.getByRole("button", { name: "Переделать" }).click();
    const compose = screen.locator(".ad-compose");
    await expect(compose).toBeVisible();

    // Валидация фактов: выдуманные факты блокируются сервисом без обращения к ИИ
    const customInput = compose.getByPlaceholder("Сделайте упор на запись сегодня");
    await customInput.fill("Скидка 80% всем новым клиентам");
    await compose.getByRole("button", { name: "Сделать" }).click();
    await expect(compose.getByRole("alert")).toContainText("сначала добавьте это в профиль");

    // Выбираем валидное изменение через чип «Короче»
    await compose.getByRole("button", { name: "Короче" }).click();

    // Ожидаем генерацию и проверку вариантов писателем
    const radiogroup = compose.getByRole("radiogroup", { name: "Новые варианты" });
    await expect(radiogroup).toBeVisible({ timeout: 20_000 });
    await expect(radiogroup.getByRole("radio").first()).toBeVisible();

    // Первый вариант выбран по умолчанию, выбираем его и нажимаем «Поставить в показы»
    await radiogroup.getByRole("radio").first().check();
    const submitBtn = compose.getByRole("button", { name: "Поставить в показы" });
    await expect(submitBtn).toBeEnabled();
    await submitBtn.click();

    // Проверяем нотис об очереди и закрытие формы
    await expect(notice(page)).toContainText(/старое объявление работает|поставлен в очередь/);
    await expect(screen.locator(".ad-compose")).toHaveCount(0);
  });

  test("3. Живой кабинет (Настройки): фото для объявлений — чекбокс согласия, загрузка и удаление", async ({ page }) => {
    await page.goto("/");
    await page.locator(".ah-rail").getByRole("button", { name: "Настройки" }).click();

    const photosSection = page.locator(".set-section[aria-labelledby='set-photos-title']");
    await expect(photosSection).toBeVisible();

    const consentCheckbox = photosSection.getByRole("checkbox", { name: /Люди на фото согласны/ });
    await expect(consentCheckbox).toBeVisible();

    const addPhotoBtn = photosSection.getByRole("button", { name: "Добавить фото" });
    // Без чекбокса кнопка добавления заблокирована
    await expect(addPhotoBtn).toBeDisabled();

    // Ставим согласие
    await consentCheckbox.check();
    await expect(addPhotoBtn).toBeEnabled();

    // Загружаем тестовое фото через инпут файла
    const fileInput = photosSection.getByLabel("Файл фото");
    await fileInput.setInputFiles(TEST_IMAGE_PATH);

    // Фото появилось в списке
    const photosList = photosSection.locator(".set-photos");
    await expect(photosList).toBeVisible({ timeout: 15_000 });
    const photoItem = photosList.locator(".set-photo").first();
    await expect(photoItem).toBeVisible();
    await expect(photoItem.locator("img")).toBeVisible();

    // Нажимаем «Убрать фото»
    const removeBtn = photoItem.getByRole("button", { name: /Убрать фото/ });
    await expect(removeBtn).toBeVisible();
    await removeBtn.click();

    // Статус об архивации фото
    await expect(photosSection.getByRole("status")).toContainText("Фото убрал из подбора");
  });
});
