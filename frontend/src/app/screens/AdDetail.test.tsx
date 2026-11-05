import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { AppShell } from "../AppShell";
import { draftAd, initialState, Store } from "../store";

const button = (name: string | RegExp) => screen.getByRole("button", { name });

function openFirst() {
  const store = new Store();
  store.goView("ads");
  render(<AppShell store={store} />);
  return store;
}

describe("экран объявления", () => {
  it("открывается из списка и показывает цифры, факты и историю", async () => {
    openFirst();
    await userEvent.click(button("Открыть «Первый визит со скидкой»"));
    expect(screen.getByRole("heading", { level: 2, name: "Первый визит со скидкой" })).toBeInTheDocument();
    expect(screen.getByText(/7 обращений/)).toBeInTheDocument();
    expect(screen.getByText("Скидка из профиля, часы с сайта")).toBeInTheDocument();
    expect(screen.getByText(/Передал 3\s000/)).toBeInTheDocument();
  });

  it("открывается из статистики", async () => {
    const store = new Store();
    store.goView("stats");
    render(<AppShell store={store} />);
    await userEvent.click(button("Открыть «Педикюр рядом с домом»"));
    expect(store.state.view).toBe("ads");
    expect(store.state.openAd).toBe("a3");
  });

  it("открывается из обзора, а новое объявление без обращений не ломает таблицу", async () => {
    const store = new Store();
    const made = draftAd(store.state, null, "Педикюр");
    if (!("ad" in made)) throw new Error(made.refusal);
    store.run({ action: "adSave", value: made.ad });
    render(<AppShell store={store} />);
    expect(screen.queryByText(/NaN/)).toBeNull();
    await userEvent.click(button("Открыть «Педикюр рядом с домом»"));
    expect(store.state.openAd).toBe("a3");
  });

  it("при смене раздела экран объявления закрывается", () => {
    const store = new Store();
    store.openAdScreen("a1");
    store.goView("stats");
    expect(store.state.openAd).toBeNull();
  });
});

describe("переделка объявления", () => {
  it("форма не закрывается, когда подробности объявления пришли позже", async () => {
    const store = openFirst();
    await userEvent.click(button("Открыть «Первый визит со скидкой»"));
    await userEvent.click(button("Переделать"));
    act(() => store.set({ ads: store.state.ads.map((a) => ({ ...a, facts: "Адрес" })) }));
    expect(button("Короче")).toBeInTheDocument();
  });

  it("новый вариант виден рядом со старым и не применяется без подтверждения", async () => {
    const store = openFirst();
    await userEvent.click(button("Открыть «Первый визит со скидкой»"));
    await userEvent.click(button("Переделать"));
    await userEvent.click(button("Короче"));
    const fresh = screen.getByRole("region", { name: "Новый вариант" });
    expect(within(fresh).getByText("Скидка на первый визит.")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Сейчас в показах" })).toHaveTextContent("Запись на сегодня");
    await userEvent.click(button("Поставить в показы"));
    expect(store.state.ads[0]!.text).toContain("Запись на сегодня");
    expect(screen.getByRole("group", { name: "Подтверждение" })).toHaveTextContent(/проверяет новый/);
    await userEvent.click(button("Применить"));
    expect(store.state.ads[0]!.text).toBe("Скидка на первый визит.");
  });

  it("предупреждает, что переделка останавливает идущую проверку вариантов", () => {
    const store = new Store();
    const ad = draftAd(store.state, store.state.ads[0]!, "Короче");
    store.ask({ action: "adSave", value: "ad" in ad ? ad.ad : store.state.ads[0]! });
    render(<AppShell store={store} />);
    expect(screen.getByRole("group", { name: "Подтверждение" })).toHaveTextContent(/проверка вариантов/i);
  });

  it("отмена из истории возвращает прежний текст и пишет новую запись", () => {
    const store = new Store();
    const old = store.state.ads[0]!.text;
    const made = draftAd(store.state, store.state.ads[0]!, "Короче");
    if (!("ad" in made)) throw new Error(made.refusal);
    store.run({ action: "adSave", value: made.ad });
    const entry = store.state.hist[0]!;
    expect(entry.canUndo).toBe(true);
    const count = store.state.hist.length;
    store.run({ action: "undo", value: entry.id });
    expect(store.state.ads[0]!.text).toBe(old);
    expect(store.state.hist).toHaveLength(count + 1);
  });
});

describe("новое объявление", () => {
  it("появляется после подтверждения, бюджет не меняется", async () => {
    const store = openFirst();
    const before = store.state.ads.length;
    await userEvent.click(button("Новое объявление"));
    await userEvent.click(button("Педикюр"));
    await userEvent.click(button("Поставить в показы"));
    expect(store.state.ads).toHaveLength(before);
    expect(screen.getByRole("group", { name: "Подтверждение" })).toHaveTextContent(/бюджет не вырастет/i);
    await userEvent.click(button("Применить"));
    expect(store.state.ads).toHaveLength(before + 1);
    expect(store.state.f.budget).toBe(20000);
    expect(store.state.hist[0]!.title).toMatch(/Добавил/);
  });

  it("отмена отключает новое объявление, а не удаляет его", () => {
    const store = new Store();
    const made = draftAd(store.state, null, "Педикюр");
    if (!("ad" in made)) throw new Error(made.refusal);
    store.run({ action: "adSave", value: made.ad });
    store.run({ action: "undo", value: store.state.hist[0]!.id });
    expect(store.state.ads.some((a) => a.id === made.ad.id)).toBe(true);
    expect(store.state.adsOff[made.ad.id]).toBe(true);
  });

  it("создаётся и словами в чате, тоже через подтверждение", () => {
    const store = new Store();
    const before = store.state.ads.length;
    store.send("сделай новое объявление про педикюр");
    expect(store.state.ads).toHaveLength(before);
    expect(store.state.pending?.action).toBe("adSave");
    store.send("да");
    expect(store.state.ads).toHaveLength(before + 1);
  });
});

describe("факты только из профиля", () => {
  it("отказывает в скидке, которой нет в профиле", () => {
    const s = initialState();
    const made = draftAd(s, s.ads[0]!, "напиши скидка 30%");
    expect(made).toEqual({ refusal: expect.stringContaining("30") });
  });

  it("отказ виден на экране, а поставить нечего", async () => {
    openFirst();
    await userEvent.click(button("Новое объявление"));
    await userEvent.type(screen.getByLabelText(/своими словами/), "скидка 30%");
    await userEvent.click(button("Сделать"));
    expect(screen.getByRole("alert")).toHaveTextContent("30%");
    expect(button("Поставить в показы")).toBeDisabled();
  });

  it("чат тоже отказывает и ничего не предлагает применить", () => {
    const store = new Store();
    store.send("сделай новое объявление со скидкой 30%");
    expect(store.state.pending).toBeNull();
    expect(store.state.msgs.at(-1)?.text).toContain("профил");
  });

  it("принимает скидку из профиля", () => {
    const s = initialState();
    expect(draftAd(s, s.ads[0]!, "напиши про скидку 20%")).toHaveProperty("ad");
  });

  it("новая цена из профиля принимается, старая нет", () => {
    const s = { ...initialState(), price: 1400, facts: initialState().facts.map((f) => (f.id === "prices" ? { ...f, value: "Маникюр от 1 400 ₽, педикюр от 1 800 ₽" } : f)) };
    expect(draftAd(s, s.ads[1]!, "маникюр за 1 400 ₽")).toHaveProperty("ad");
    expect(draftAd(s, s.ads[1]!, "маникюр за 900 ₽")).toHaveProperty("refusal");
  });

  it("отзыв требует согласия", () => {
    const s = initialState();
    expect(draftAd(s, s.ads[0]!, "добавь отзыв клиентки")).toEqual({ refusal: expect.stringContaining("соглас") });
  });

  it("убранный из профиля факт не попадает в готовый вариант", () => {
    const s = { ...initialState(), facts: initialState().facts.filter((f) => f.id !== "prices") };
    for (let i = 0; i < 4; i += 1) {
      const made = draftAd(s, null, "Педикюр", i);
      if ("ad" in made) expect(`${made.ad.hook} ${made.ad.text}`).not.toMatch(/₽/);
    }
  });

  it("фото нельзя поставить без согласия на съёмку", async () => {
    openFirst();
    await userEvent.click(button("Открыть «Первый визит со скидкой»"));
    await userEvent.click(button("Переделать"));
    const file = new File(["x"], "работа.jpg", { type: "image/jpeg" });
    URL.createObjectURL = () => "blob:photo";
    await userEvent.upload(screen.getByLabelText("Своё фото"), file);
    expect(button("Поставить в показы")).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: /согласие/ }));
    expect(button("Поставить в показы")).toBeEnabled();
  });
});

describe("запуск", () => {
  it("объявление можно переделать до запуска, без подтверждения и записи в историю", async () => {
    const store = new Store(initialState("start"));
    act(() => store.set({ step: 3 }));
    const before = store.state.hist.length;
    render(<AppShell store={store} />);
    await userEvent.click(button("Переделать «Первый визит со скидкой»"));
    await userEvent.click(button("Короче"));
    await userEvent.click(button("Взять этот вариант"));
    expect(store.state.ads[0]!.text).toBe("Скидка на первый визит.");
    expect(store.state.hist).toHaveLength(before);
  });

  it("AdMaker кнопка назад возвращает в список", async () => {
    cleanup();
    const store = new Store();
    store.openAdScreen("new");
    render(<AppShell store={store} />);
    expect(screen.getByRole("heading", { level: 2, name: "Новое объявление" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Все объявления" }));
    expect(store.state.openAd).toBeNull();
  });

  it("кнопка «Вся история и отмена» и включение отключённого объявления", async () => {
    cleanup();
    const store = new Store();
    store.goView("ads");
    store.set({ adsOff: { a1: true } });
    render(<AppShell store={store} />);
    await userEvent.click(button("Открыть «Первый визит со скидкой»"));

    // Кнопка перехода во всю историю
    await userEvent.click(screen.getByRole("button", { name: "Вся история и отмена" }));
    expect(store.state.view).toBe("history");

    // Возврат в объявление и включение отключённого
    act(() => store.openAdScreen("a1"));
    await userEvent.click(screen.getByRole("button", { name: "Включить" }));
    expect(store.state.adsOff.a1).toBe(false);
  });

  it("Compose: кнопка «Другой вариант»", async () => {
    cleanup();
    openFirst();
    await userEvent.click(button("Открыть «Первый визит со скидкой»"));
    await userEvent.click(button("Переделать"));
    await userEvent.click(button("Другой акцент"));
    const otherBtn = await screen.findByRole("button", { name: "Другой вариант" });
    await userEvent.click(otherBtn);
    expect(screen.getByRole("button", { name: "Поставить в показы" })).toBeEnabled();
  });

  it("при s.live и store.rework открывает LiveRework и обрабатывает ошибку выбора варианта", async () => {
    cleanup();
    const store = new Store();
    store.goView("ads");
    store.openAdScreen("a1");
    const reworkApi = {
      rework: vi.fn().mockResolvedValue({ id: 10, token: "tok10", status: "ready" }),
      reworkSet: vi.fn().mockResolvedValue({
        id: 10, status: "ready", template: true, needs_review: false,
        variants: [{ id: 101, headline: "Новый заголовок", text: "Новый текст", numbers: [], sources: [] }],
      }),
      chooseRework: vi.fn().mockRejectedValue(new Error("Не удалось выбрать вариант")),
    };
    store.rework = reworkApi;
    store.set({ live: { budget: 20000, spent: 5000, alerts: [] } as any });
    render(<AppShell store={store} />);

    await userEvent.click(button("Переделать"));
    expect(screen.getByLabelText("Переделать объявление")).toBeInTheDocument();

    // Запрашиваем "Короче"
    await userEvent.click(screen.getByRole("button", { name: "Короче" }));
    await userEvent.click(await screen.findByRole("radio"));
    const chooseBtn = screen.getByRole("button", { name: "Поставить в показы" });
    await userEvent.click(chooseBtn);
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось выбрать вариант");

    // Отмена переделки возвращает экран к карточке
    await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(screen.queryByLabelText("Переделать объявление")).toBeNull();

    // Успешный выбор варианта через LiveRework
    reworkApi.chooseRework.mockResolvedValueOnce({ text: "Новый вариант выбран!" });
    await userEvent.click(button("Переделать"));
    await userEvent.click(screen.getByRole("button", { name: "Короче" }));
    await userEvent.click(await screen.findByRole("radio"));
    await userEvent.click(screen.getByRole("button", { name: "Поставить в показы" }));
    expect(store.state.notice).toBe("Новый вариант выбран!");

    // Кнопка "Все объявления"
    await userEvent.click(screen.getByRole("button", { name: "Все объявления" }));
    expect(store.state.openAd).toBeNull();
  });

  it("проверяет скрытие кнопки «Переделать» без rework, пустые факты и сбой LiveRework", async () => {
    cleanup();
    const store = new Store();
    store.goView("ads");
    store.openAdScreen("a1");

    // s.live без store.rework: кнопка "Переделать" не отображается, пустые факты -> "Проверяю"
    store.rework = null;
    const emptyFactsAd = { ...store.state.ads[0]!, facts: "" };
    store.set({
      live: { budget: 20000, spent: 5000, alerts: [] } as any,
      ads: [emptyFactsAd, ...store.state.ads.slice(1)],
    });
    render(<AppShell store={store} />);
    expect(screen.queryByRole("button", { name: "Переделать" })).toBeNull();
    expect(screen.getByText("Проверяю")).toBeInTheDocument();

    // LiveRework: отправка текста через форму и статус failed
    cleanup();
    const storeRework = new Store();
    storeRework.goView("ads");
    storeRework.openAdScreen("a1");
    storeRework.waitMs = 1;
    storeRework.set({ live: { budget: 20000, spent: 5000, alerts: [] } as any });
    const mockRework = {
      rework: vi.fn().mockResolvedValue({ id: 99, token: "tok99", status: "pending" }),
      reworkSet: vi.fn().mockResolvedValue({ id: 99, status: "failed", problem: "Текст отклонён", variants: [] }),
      chooseRework: vi.fn(),
    };
    storeRework.rework = mockRework;
    render(<AppShell store={storeRework} />);
    await userEvent.click(button("Переделать"));

    const input = screen.getByLabelText(/Или напишите своими словами/);
    await userEvent.type(input, "Сделай короче и ярче");
    await userEvent.click(screen.getByRole("button", { name: "Сделать" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Текст отклонён");
  });
});
