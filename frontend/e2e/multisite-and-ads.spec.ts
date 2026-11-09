import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const nav = (page: Page, name: string) => page.locator(".ah-rail").getByRole("button", { name });
const notice = (page: Page) => page.locator(".ah-notice");

test.describe.configure({ mode: "serial" });

test.describe("E2E Юзеркейсы: Мультибизнес, генерация объявлений и бюджет (multisite-and-ads.spec.ts)", () => {
  test("UC-1: Прямой вход вернувшегося пользователя и пропуск первичного опроса", async ({ page }) => {
    // Пользователь с имеющимися бизнесами открывает стартовую страницу
    await page.goto("/?start");

    // У пользователя стенда есть бизнес в сессии: после проверки сессии он попадает в кабинет, минуя опрос.
    await expect(page.locator(".ah-rail")).toBeVisible();
    await expect(page.locator(".ah-brand")).toBeVisible();
    await expect(page.locator(".st-auth-box")).toHaveCount(0);
  });

  test("UC-2: Переключение между бизнесами через селектор в шапке", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator(".ah-brand")).toBeVisible();

    // 1. Проверяем кнопку выпадающего списка выбора сайта
    const selectorBtn = page.locator(".ah-brand-selector .ah-brand-btn");
    await expect(selectorBtn).toBeVisible();
    await expect(selectorBtn).toHaveAttribute("aria-haspopup", "true");

    // 2. Раскрываем меню
    await selectorBtn.click();
    const menu = page.locator(".ah-brand-menu");
    await expect(menu).toBeVisible();
    await expect(menu.locator(".ah-brand-menu-header")).toHaveText("Ваши бизнесы");

    // 3. Меню содержит список сайтов и действие добавления нового
    const items = menu.locator(".ah-brand-menu-item");
    const count = await items.count();
    expect(count).toBeGreaterThanOrEqual(1);

    // Активный бизнес отмечен галочкой
    await expect(items.first().locator(".ah-brand-check")).toContainText("✓");

    // 4. Закрытие меню кликом вне его
    await page.locator("body").click({ position: { x: 10, y: 10 } });
    await expect(menu).toHaveCount(0);
  });

  test("UC-3: Добавление нового сайта без конфликтов и блокировок", async ({ page }) => {
    await page.goto("/");

    // 1. Открываем меню брендов и нажимаем «+ Добавить сайт»
    await page.locator(".ah-brand-selector .ah-brand-btn").click();
    const addSiteBtn = page.locator(".ah-brand-menu-action");
    await expect(addSiteBtn).toBeVisible();
    await expect(addSiteBtn).toContainText("+ Добавить бизнес");

    // 2. Клик переводит на экран добавления нового сайта (?start=new)
    await addSiteBtn.click();
    await expect(page).toHaveURL(/\/\?start=new/);
    await expect(page.getByRole("heading", { level: 1, name: "Где о вас можно прочитать?" })).toBeVisible();

    // 3. Вводим URL нового проекта
    const input = page.getByLabel("Ссылка на сайт или страницу");
    await input.fill("https://barber-perm.ru");
    await page.getByRole("button", { name: "Изучить" }).click();

    // При успешном переходе система переходит к шагам профиля
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  });

  test("UC-4: Генерация новых объявлений с указанием услуги и пожеланий пользователя", async ({ page }) => {
    await page.goto("/");
    await nav(page, "Реклама").click();

    // 1. Кнопка «Сгенерировать объявления» доступна на экране Реклама
    const genBtn = page.getByRole("button", { name: "Сгенерировать объявления" });
    await expect(genBtn).toBeVisible();
    await genBtn.click();

    // 2. Открылось модальное окно генерации объявлений
    const modal = page.locator(".ah-modal");
    await expect(modal).toBeVisible();
    await expect(modal.locator(".ah-modal-title")).toHaveText("Сгенерировать новые объявления");

    // 3. Выбор услуги (чипы существующих услуг или ввод новой)
    const chips = modal.locator(".ah-chips button");
    if ((await chips.count()) > 0) {
      await chips.first().click();
      await expect(chips.first()).toHaveAttribute("aria-pressed", "true");
    }

    // 4. Ввод свободных пожеланий (если доступен в режиме прототипа)
    const promptInput = modal.locator("textarea");
    if ((await promptInput.count()) > 0) {
      await promptInput.fill("Скидка 20% новым клиентам в будние дни до 14:00");
    }

    // 5. Запуск генерации
    const submitGen = modal.getByRole("button", { name: "Сгенерировать через ИИ" });
    await expect(submitGen).toBeVisible();
    await submitGen.click();

    // 6. Появление сгенерированных карточек объявлений (воркер или быстрый шаблон)
    const previewCards = modal.getByRole("article");
    await expect(previewCards.first()).toBeVisible({ timeout: 20_000 });
    expect(await previewCards.count()).toBeGreaterThanOrEqual(1);

    // 7. Карточки имеют чекбоксы для выбора
    const firstCheckbox = previewCards.first().locator("input[type='checkbox']");
    await expect(firstCheckbox).toBeChecked();

    // 8. Добавление выбранных объявлений в кабинет
    const addSelectedBtn = modal.getByRole("button", { name: /Добавить выбранные/ });
    await expect(addSelectedBtn).toBeVisible();
    await addSelectedBtn.click();

    // Модальное окно закрылось после добавления
    await expect(modal).toHaveCount(0);
    await expect(notice(page)).toContainText(/Добавил \d+ объявлен/);
  });

  test("UC-5: Управление распределением рекламного бюджета (раздельный / общий)", async ({ page }) => {
    await page.goto("/");

    // Переходим в настройки кабинета
    await page.locator(".ah-rail").getByRole("button", { name: "Настройки" }).click();
    await expect(page.locator(".set")).toBeVisible();

    const budgetForm = page.getByRole("form", { name: "Бюджет месяца" });
    await expect(budgetForm).toBeVisible();

    // 1. Проверяем наличие переключателя режима бюджета
    const sepChip = budgetForm.getByRole("button", { name: "Раздельный на каждый бизнес" });
    const sharedChip = budgetForm.getByRole("button", { name: "Общий на все бизнесы" });
    await expect(sepChip).toBeVisible();
    await expect(sharedChip).toBeVisible();

    // 2. Переключаем на общий бюджет
    await sharedChip.click();
    await expect(sharedChip).toHaveAttribute("aria-pressed", "true");
    await expect(sepChip).toHaveAttribute("aria-pressed", "false");

    // 3. Отображается поле ввода общего бюджета и пояснение
    const budgetInput = budgetForm.locator("input[inputmode='numeric']");
    await expect(budgetInput).toBeVisible();
    await budgetInput.fill("60000");

    // 4. Применяем общий бюджет
    await budgetForm.getByRole("button", { name: "Изменить бюджет" }).click();
    await expect(budgetForm.locator(".set-confirm")).toBeVisible();
    await budgetForm.getByRole("button", { name: "Применить" }).click();
    // Лимиты сайтов ставятся по одному через сервис: ждём итог, а не гоним дальше.
    await expect(notice(page)).toContainText(/Общий бюджет .* сохранён|Остановился на сайте/, { timeout: 30_000 });

    // 5. Возврат в раздельный режим
    await sepChip.click();
    await expect(sepChip).toHaveAttribute("aria-pressed", "true");
    await budgetInput.fill("20000");
    await budgetForm.getByRole("button", { name: "Изменить бюджет" }).click();
    await budgetForm.getByRole("button", { name: "Применить" }).click();
  });

  test("UC-6: Удаление бизнеса в настройках и предупреждение", async ({ page }) => {
    await page.goto("/");
    await page.locator(".ah-rail").getByRole("button", { name: "Настройки" }).click();
    await expect(page.locator(".set")).toBeVisible();

    const delBtn = page.getByRole("button", { name: "Удалить бизнес" });
    await expect(delBtn).toBeVisible();
    await delBtn.click();

    const confirmGroup = page.getByRole("group", { name: "Подтверждение удаления бизнеса" });
    await expect(confirmGroup).toBeVisible();
    await expect(confirmGroup.getByText("Это действие нельзя отменить")).toBeVisible();

    // Отмена скрывает подтверждение
    await confirmGroup.getByRole("button", { name: "Отмена" }).click();
    await expect(confirmGroup).toHaveCount(0);
    await expect(delBtn).toBeVisible();
  });

  test("UC-7: Управление объявлениями: табы статусов (Все, К запуску/Работают, Отключены, В архиве), архивация и восстановление", async ({ page }) => {
    await page.goto("/");
    await page.locator(".ah-rail").getByRole("button", { name: "Реклама" }).click();
    await expect(page.locator(".ann")).toBeVisible();

    // 1. Проверяем табы фильтров
    const filterGroup = page.getByRole("group", { name: "Какие показать" });
    await expect(filterGroup).toBeVisible();
    const tabAll = filterGroup.getByRole("button", { name: /^Все/ });
    const tabActive = filterGroup.getByRole("button", { name: /^Работают/ });
    const tabOff = filterGroup.getByRole("button", { name: /^Отключены/ });
    const tabArchived = filterGroup.getByRole("button", { name: /^В архиве/ });

    await expect(tabAll).toBeVisible();
    await expect(tabActive).toBeVisible();
    await expect(tabOff).toBeVisible();
    await expect(tabArchived).toBeVisible();

    // 2. Открываем первое объявление и архивируем его
    const openBtn = page.getByRole("button", { name: /^Открыть объявление «/ }).first();
    await expect(openBtn).toBeVisible();
    await openBtn.click();
    await expect(page.locator(".anno-screen")).toBeVisible();

    // Нажимаем «В архив» и подтверждаем действие через Proposal pattern (.ah-confirm)
    const archiveBtn = page.getByRole("button", { name: "В архив" });
    await expect(archiveBtn).toBeVisible();
    await archiveBtn.click();

    const confirmBox = page.locator(".ah-confirm");
    await expect(confirmBox).toBeVisible();
    await confirmBox.getByRole("button", { name: "Применить" }).click();
    await expect(confirmBox).toHaveCount(0);

    // Возвращаемся к списку объявлений
    await page.getByRole("button", { name: "Все объявления" }).click();
    await expect(page.locator(".ann")).toBeVisible();

    // 3. Переключаемся на таб «В архиве»
    await tabArchived.click();
    await expect(tabArchived).toHaveAttribute("aria-pressed", "true");

    // 4. Восстанавливаем объявление из архива: «Вернуть из архива» стоит на его экране
    await page.getByRole("button", { name: /^Открыть объявление «/ }).first().click();
    const restoreBtn = page.getByRole("button", { name: "Вернуть из архива" });
    await expect(restoreBtn).toBeVisible();
    await restoreBtn.click();

    await expect(confirmBox).toBeVisible();
    await confirmBox.getByRole("button", { name: "Применить" }).click();
    await expect(confirmBox).toHaveCount(0);
    await page.getByRole("button", { name: "Все объявления" }).click();

    // 5. Переключаемся на «Все» — карточка снова в основном списке
    await tabAll.click();
    await expect(page.getByRole("button", { name: /^Открыть объявление «/ }).first()).toBeVisible();
  });
});
