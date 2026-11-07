import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const nav = (page: Page, name: string) => page.locator(".ah-rail").getByRole("button", { name });

test.describe.configure({ mode: "serial" });

test.describe("Граничные и аварийные ситуации кабинета (edge-situations.spec.ts)", () => {
  test("1. Ситуация «нет денег»: индикатор тревоги, кнопка «Я пополнил», переход в ожидание зачисления", async ({ page }) => {
    await page.goto("/?mock");

    // Переключаемся на ситуацию «нет денег»
    await page.locator(".ah-proto").getByRole("button", { name: "нет денег" }).click();

    // 1. Статус в шапке сообщает об остановке показов
    const status = page.locator(".ah-status");
    await expect(status).toBeVisible();
    await expect(status).toHaveText(/Показы остановлены|Жду деньги/);

    // 2. Карточка тревоги в блоке внимания
    const attention = page.locator(".ov-attention");
    await expect(attention).toBeVisible();
    await expect(attention.getByRole("heading", { level: 2 })).toContainText("На счёте в Яндексе 0 ₽, показы стоят");
    await expect(attention).toContainText("salon-primer");

    // 3. Нажатие кнопки «Я пополнил»
    const topUpBtn = attention.getByRole("button", { name: "Я пополнил" });
    await expect(topUpBtn).toBeVisible();
    await topUpBtn.click();

    // 4. Текст меняется на ожидание дохождения средств
    await expect(attention.getByRole("heading", { level: 2 })).toContainText("Жду, когда деньги дойдут");
    await expect(attention).toContainText("обычно за 5 минут");
  });

  test("2. Ситуация «отказ Яндекса»: карточка причины отклонения и переход в историю", async ({ page }) => {
    await page.goto("/?mock");
    await page.locator(".ah-proto").getByRole("button", { name: "отказ Яндекса" }).click();

    // 1. Карточка внимания сообщает об отклонении и исправлении
    const attention = page.locator(".ov-attention");
    await expect(attention).toBeVisible();
    await expect(attention.getByRole("heading", { level: 2 })).toContainText("Яндекс отклонил объявление, я уже исправил");
    await expect(attention).toContainText("не хватало адреса");

    // 2. Клик по кнопке «Посмотреть в истории»
    await attention.getByRole("button", { name: "Посмотреть в истории" }).click();

    // 3. Переход в раздел «История» с записью об исправлении
    await expect(page.locator(".ah-hist")).toBeVisible();
    await expect(page.getByRole("heading", { name: /Исправил.*после отказа Яндекса/ }).first()).toBeVisible();
  });

  test("3. Ситуация «бюджет на исходе»: предупреждение, опции «Оставить» и «Добавить 3 000 ₽»", async ({ page }) => {
    await page.goto("/?mock");
    await page.locator(".ah-proto").getByRole("button", { name: "бюджет на исходе" }).click();

    // 1. Статус в шапке предупреждает о расходе
    const status = page.locator(".ah-status");
    await expect(status).toHaveText(/Бюджет на исходе|Нужно внимание/);

    // 2. Карточка внимания
    const attention = page.locator(".ov-attention");
    await expect(attention).toBeVisible();
    await expect(attention.getByRole("heading", { level: 2 })).toContainText(/Осталось .* на .* дня/);

    // 3. Выбор опции «Оставить»
    await attention.getByRole("button", { name: "Оставить" }).click();
    await expect(attention.getByRole("heading", { level: 2 })).toContainText("Бюджет оставили как есть");

    // 4. Повторное переключение и выбор «Добавить 3 000 ₽»
    await page.locator(".ah-proto").getByRole("button", { name: "бюджет на исходе" }).click();
    const addBtn = page.locator(".ov-attention").getByRole("button", { name: /Добавить/ });
    await expect(addBtn).toBeVisible();
    await addBtn.click();

    // Бюджет увеличен, блок спокоен
    await expect(page.locator(".ov-attention")).toContainText("Всё в порядке");
  });

  test("4. Ситуация «нет доступа»: индикатор потери токена и кнопка переподключения", async ({ page }) => {
    await page.goto("/?mock");
    await page.locator(".ah-proto").getByRole("button", { name: "нет доступа" }).click();

    // 1. Статус в шапке
    await expect(page.locator(".ah-status")).toHaveText("Нет доступа");

    // 2. Карточка блокировки изменений
    const attention = page.locator(".ov-attention");
    await expect(attention.getByRole("heading", { level: 2 })).toContainText("Пропал доступ к кабинету Яндекса");
    await expect(attention).toContainText("остановил все изменения");

    // 3. Кнопка «Подключить заново»
    await expect(attention.getByRole("button", { name: "Подключить заново" })).toBeVisible();
  });

  test("5. Ситуация «первые дни»: статус «Собираю данные», скрытие преждевременных оценок", async ({ page }) => {
    await page.goto("/?mock");
    await page.locator(".ah-proto").getByRole("button", { name: "первые дни" }).click();

    // 1. Статус «Собираю данные»
    await expect(page.locator(".ah-status")).toHaveText("Собираю данные");

    // 2. Карточка раннего этапа
    const attention = page.locator(".ov-attention");
    await expect(attention.getByRole("heading", { level: 2 })).toContainText(/День .* из примерно 14: выводы делать рано/);

    // 3. В таблице объявлений бейджи «собираю данные»
    const badge = page.locator(".ov-anno-cell .ah-tag").first();
    await expect(badge).toHaveText("собираю данные");

    // 4. В разделе «Реклама» нет ложных выводов об эффективности
    await nav(page, "Реклама").click();
    await expect(page.locator(".ann-badge")).toHaveCount(0);
  });

  test("6. Ситуация «вывод проверки»: победа A/B варианта креатива и отмена решения", async ({ page }) => {
    await page.goto("/?mock");
    await page.locator(".ah-proto").getByRole("button", { name: "вывод проверки" }).click();

    // 1. Карточка итога A/B теста
    const attention = page.locator(".ov-attention");
    await expect(attention.getByRole("heading", { level: 2 })).toContainText("Проверка закончена: скидка лучше цены");
    await expect(attention).toContainText("Уверенность высокая");

    // 2. Переход к просмотру в истории
    await attention.getByRole("button", { name: "Посмотреть и отменить" }).click();
    await expect(page.locator(".ah-hist")).toBeVisible();
    await expect(page.getByText(/Отключил вариант/).first()).toBeVisible();
  });

  test("7. Живой кабинет: сверка с Яндексом и подтверждение баланса через API", async ({ page, context }) => {
    // Очищаем ah_guest, чтобы Vite проксировал живую демо-сессию
    await context.clearCookies();
    await page.goto("/");
    await expect(page.locator(".ah-brand")).toHaveText("Студия маникюра «Лак»");

    // 1. Переход в статистику
    await nav(page, "Статистика").click();
    await expect(page.locator(".stats-check-note")).toBeVisible();
    await expect(page.locator(".stats-check-note")).toContainText(/Яндекс уточняет данные/);

    // 2. Проверка ответа endpoint сверки баланса
    const businessResp = await page.request.get("/api/businesses");
    const businesses = await businessResp.json();
    const businessId = businesses[0].id;

    const meResp = await page.request.get("/api/me");
    const { csrf_token } = await meResp.json();

    const confirmResp = await page.request.post(`/api/businesses/${businessId}/balance/confirm`, {
      data: { account: "lak-studio" },
      headers: { "X-CSRF-Token": csrf_token },
    });
    expect(confirmResp.ok()).toBe(true);
    expect((await confirmResp.json()).confirmed).toBe(true);
  });
});
