import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const nav = (page: Page, name: string) => page.locator(".ah-rail").getByRole("button", { name });
const notice = (page: Page) => page.locator(".ah-notice");
const STAND = "http://localhost:8011";

test.describe.configure({ mode: "serial" });

test.describe("Жизненный цикл A/B-тестирования и решений (ab-decisions.spec.ts)", () => {
  test("1. Автопилот: предложение A/B-теста, согласие, подготовка и отмена владельцем", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator(".ah-brand")).toHaveText("Студия маникюра «Лак»");

    const business = (await (await page.request.get("/api/businesses")).json())[0];
    const { csrf_token: csrf } = await (await page.request.get("/api/me")).json();
    const base = `/api/businesses/${business.id}`;
    const changes = async () =>
      ((await (await page.request.get(`${base}/history`)).json()) as { ref: string }[]).filter((line) => line.ref.startsWith("change-")).length;
    const changesBefore = await changes();

    // Задаём цель стоимости обращения 300 ₽, чтобы автопилот нашёл объявление с перерасходом
    const goal = await page.request.post(`${base}/goal`, {
      data: { rubles: 300 },
      headers: { "X-CSRF-Token": csrf },
    });
    expect(goal.ok()).toBe(true);

    // Запускаем суточный цикл автопилота через стенд
    const run = await page.request.post(`${STAND}/api/stand/businesses/${business.id}/autopilot`);
    expect((await run.json()).accounts).toBe(1);

    await page.reload();

    // 1. Появление карточки с предложением проверить новый вариант
    const offer = page.locator(".ov-attention, .ov-more-item").filter({
      has: page.getByRole("heading", { name: "Предлагаю проверить новый вариант объявления" }),
    });
    await expect(offer).toBeVisible();
    await expect(offer).toContainText("«Маникюр у метро Профсоюзная»");
    await expect(offer).toContainText("Бюджет не меняется");

    // 2. Владелец соглашается на A/B тест
    await offer.getByRole("button", { name: "Проверить вариант" }).click();
    await expect(notice(page)).toContainText("Готовлю тест. Пока вариант не отправлен, объявление работает как раньше");

    // 3. После перезагрузки предложение переходит в карточку подготовки A/B теста
    await page.reload();
    await expect(page.getByRole("heading", { name: "Предлагаю проверить новый вариант объявления" })).toHaveCount(0);
    const preparing = page.locator(".ov-attention, .ov-more-item").filter({
      has: page.getByRole("heading", { name: "Готовлю A/B-тест" }),
    });
    await expect(preparing).toBeVisible();
    await expect(preparing).toContainText("работает как раньше");

    // 4. Владелец передумывает и отменяет тест
    await preparing.getByRole("button", { name: "Отменить тест" }).click();
    await expect(notice(page)).toContainText("Тест отменён. Показы не менялись, это объявление не предложу две недели");

    await page.reload();
    await expect(page.getByRole("heading", { name: "Готовлю A/B-тест" })).toHaveCount(0);

    // Отмена — тот же отказ: следующий цикл не предлагает это объявление снова.
    await page.request.post(`${STAND}/api/stand/businesses/${business.id}/autopilot`);
    const today = await (await page.request.get(`${base}/today`)).json();
    expect(today.cards.filter((card: { kind: string }) => card.kind.startsWith("test_"))).toEqual([]);
    // Ни предложение, ни подготовка, ни отмена не меняли площадку: журнал изменений тот же.
    expect(await changes()).toBe(changesBefore);
  });

  test("2. Экран «Реклама»: отклонение объявления («Не запускать») из блока «Нужно ваше решение»", async ({ page }) => {
    await page.goto("/");
    await nav(page, "Реклама").click();

    // Блок «Нужно ваше решение»
    const decide = page.locator(".ann-decide");
    await expect(decide).toBeVisible();
    await expect(decide.getByRole("heading", { level: 2 })).toHaveText("Нужно ваше решение");

    const card = decide.locator(".ann-card").filter({ hasText: "Педикюр в студии «Лак»" });
    await expect(card).toBeVisible();

    // Нажимаем «Не запускать»
    const rejectBtn = card.getByRole("button", { name: "Не запускать" });
    await expect(rejectBtn).toBeVisible();
    await rejectBtn.click();

    // Проверяем нотис и исчезновение карточки
    await expect(notice(page)).toContainText("Не запускаю «Педикюр в студии «Лак»»");
    await expect(decide.locator(".ann-card").filter({ hasText: "Педикюр в студии «Лак»" })).toHaveCount(0);
  });

  test("3. Экран «Реклама»: отзыв подготовленного объявления из блока «Готовлю к запуску»", async ({ page }) => {
    await page.goto("/");
    await nav(page, "Реклама").click();

    // Блок «Готовлю к запуску»
    const queue = page.locator(".ann-queue");
    await expect(queue).toBeVisible();
    await expect(queue.getByRole("heading", { level: 2 })).toHaveText("Готовлю к запуску");

    const queueItem = queue.locator("li").filter({ hasText: "Снятие покрытия — 300 ₽" });
    await expect(queueItem).toBeVisible();
    await expect(queueItem).toContainText("в запасе на замену");

    // Нажимаем «Не запускать» для этого объявления
    const removeBtn = queueItem.getByRole("button", { name: /Не запускать/ });
    await expect(removeBtn).toBeVisible();
    await removeBtn.click();

    // Проверяем нотис и удаление из списка подготовки
    await expect(notice(page)).toContainText("Не запускаю «Снятие покрытия — 300 ₽»");
    await expect(queue.locator("li").filter({ hasText: "Снятие покрытия — 300 ₽" })).toHaveCount(0);
  });

  test("2. Прототип (/?mock): итог проверки A/B-теста и отмена в Истории", async ({ page }) => {
    await page.goto("/?mock");

    // Переключаемся на прототипную ситуацию «вывод проверки»
    await page.locator(".ah-proto").getByRole("button", { name: "вывод проверки" }).click();

    // Карточка итога теста
    const attention = page.locator(".ov-attention");
    await expect(attention).toBeVisible();
    await expect(attention.getByRole("heading", { level: 2 })).toContainText("Проверка закончена: скидка лучше цены");

    // Переход к просмотру и отмене
    await attention.getByRole("button", { name: "Посмотреть и отменить" }).click();

    // Экран Истории
    await expect(page.locator(".ah-hist")).toBeVisible();
    const verdictRow = page.locator(".ah-hist-row").filter({ hasText: "Отключил вариант «Маникюр от 1" });
    await expect(verdictRow).toBeVisible();

    // Клик «Отменить» открывает пояснение последствий отмены
    const undoBtn = verdictRow.getByRole("button", { name: "Отменить" });
    await expect(undoBtn).toBeVisible();
    await undoBtn.click();

    await expect(verdictRow.locator(".ah-hist-note")).toContainText("Оба варианта снова поделят бюджет поровну");
  });
});
