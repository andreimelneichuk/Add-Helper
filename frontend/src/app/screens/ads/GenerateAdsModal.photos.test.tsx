import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../../../api";
import type { Api } from "../../../api";
import { fakeApi, open } from "../../testLive";
import { GenerateAdsModal } from "./GenerateAdsModal";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const ADS = [
  { headline: "Маникюр 1200 ₽", text: "Запишитесь сегодня", facts: ["цена"], services: ["Маникюр"] },
  { headline: "Стрижка без ожидания", text: "Приходите в удобное время", facts: ["услуга"], services: ["Стрижка"] },
];
const photo = (id: number, source: "owner" | "generated" = "owner") => ({ id, width: 800, height: 800, url: `/p/${id}`, source });

function guest() {
  vi.spyOn(apiModule, "createGuestApi").mockReturnValue({
    writeAds: vi.fn(async () => ({ id: 5, token: "t", status: "queued" })),
    adSet: vi.fn(async () => ({ id: 5, status: "ready", ads: ADS })),
  } as unknown as ReturnType<typeof apiModule.createGuestApi>);
}

async function run(over: Partial<Api>) {
  guest();
  const adoptAds = vi.fn(async () => ({ kept: 2 }));
  const store = await open(fakeApi({ adoptAds, ...over } as Partial<Api>));
  render(<GenerateAdsModal isOpen onClose={vi.fn()} store={store} />);
  await userEvent.click(screen.getByRole("button", { name: "Сгенерировать через ИИ" }));
  return { adoptAds };
}

describe("фото к сгенерированным объявлениям", () => {
  it("ставит каждому объявлению фото его услуги, ничего не рисуя за деньги", async () => {
    const matchPhotos = vi.fn(async () => ({
      matches: [{ image_id: 7, missing: null }, { image_id: 8, missing: null }],
      photos: [photo(7), photo(8)],
    }));
    const generatePhoto = vi.fn();
    const { adoptAds } = await run({ matchPhotos, generatePhoto } as Partial<Api>);

    expect(await screen.findByAltText("Фото к варианту 1")).toHaveAttribute("src", "/p/7");
    expect(screen.getByAltText("Фото к варианту 2")).toHaveAttribute("src", "/p/8");
    expect(matchPhotos).toHaveBeenCalledWith([["Маникюр"], ["Стрижка"]]);
    await userEvent.click(screen.getByRole("button", { name: /Добавить выбранные/ }));

    expect(generatePhoto).not.toHaveBeenCalled();
    expect(adoptAds).toHaveBeenCalledWith({ id: 5, token: "t", kept: [0, 1], photos: { 0: 7, 1: 8 } });
  });

  it("рисует сцену только для услуги без подходящего фото", async () => {
    const matchPhotos = vi.fn(async () => ({
      matches: [{ image_id: 7, missing: null }, { image_id: null, missing: "Стрижка" }],
      photos: [photo(7)],
    }));
    const generatePhoto = vi.fn(async () => ({ id: 21, url: "/p/21", source: "generated" as const }));
    const { adoptAds } = await run({ matchPhotos, generatePhoto } as Partial<Api>);

    expect(await screen.findByAltText("Фото к варианту 2")).toHaveAttribute("src", "/p/21");
    expect(generatePhoto).toHaveBeenCalledTimes(1);
    expect(generatePhoto).toHaveBeenCalledWith("Стрижка");
    await userEvent.click(screen.getByRole("button", { name: /Добавить выбранные/ }));

    expect(adoptAds).toHaveBeenCalledWith({ id: 5, token: "t", kept: [0, 1], photos: { 0: 7, 1: 21 } });
  });

  it("если сцену сделать не вышло, называет причину и добавляет объявление без фото", async () => {
    const matchPhotos = vi.fn(async () => ({
      matches: [{ image_id: null, missing: "Маникюр" }, { image_id: null, missing: "Стрижка" }],
      photos: [],
    }));
    const generatePhoto = vi.fn(async () => { throw new Error("Сегодня уже сделано 3 фото."); });
    const { adoptAds } = await run({ matchPhotos, generatePhoto } as Partial<Api>);

    expect(await screen.findByText(/Сегодня уже сделано 3 фото\./)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: /Добавить выбранные/ })).toBeEnabled());
    // После первого отказа вторую услугу за деньги не пробуем.
    expect(generatePhoto).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: /Добавить выбранные/ }));

    expect(adoptAds).toHaveBeenCalledWith({ id: 5, token: "t", kept: [0, 1] });
  });

  it("владелец может снять или сменить фото у одного объявления", async () => {
    const matchPhotos = vi.fn(async () => ({
      matches: [{ image_id: 7, missing: null }, { image_id: 7, missing: null }],
      photos: [photo(7)],
    }));
    const { adoptAds } = await run({ matchPhotos } as Partial<Api>);
    await screen.findByAltText("Фото к варианту 1");

    // Цикл: фото → без фото для варианта 1.
    await userEvent.click(screen.getAllByRole("button", { name: "Другое фото" })[0]!);
    expect(screen.queryByAltText("Фото к варианту 1")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Добавить выбранные/ }));

    expect(adoptAds).toHaveBeenCalledWith({ id: 5, token: "t", kept: [0, 1], photos: { 1: 7 } });
  });
});
