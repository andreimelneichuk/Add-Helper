import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Api } from "../../api";
import { fakeApi, open } from "../testLive";
import { Settings } from "./Settings";

afterEach(cleanup);

describe("фото с сайта в настройках", () => {
  it("ищет фото по адресу профиля, но импортирует только после выбора и согласия", async () => {
    const profile = vi.fn(async () => ({
      services: [], address: "Пермь", features: [], niche: "салон", website: "https://salon.example",
    }));
    const siteImages = vi.fn(async () => ({
      images: [{ url: "https://salon.example/photo.jpg", preview: "data:image/jpeg;base64,AA==" }],
    }));
    const importSiteImages = vi.fn(async () => ({ added: 1, skipped: 0 }));
    const api = fakeApi({ profile, siteImages, importSiteImages } as unknown as Partial<Api>);
    render(<Settings store={await open(api)} />);

    expect(await screen.findByText(/Выберите подходящие и подтвердите права/)).toBeInTheDocument();
    expect(siteImages).toHaveBeenCalledWith("https://salon.example");
    expect(importSiteImages).not.toHaveBeenCalled();

    await userEvent.click(screen.getByLabelText("Взять картинку 1"));
    await userEvent.click(screen.getByRole("button", { name: "Добавить отмеченные" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Сначала отметьте, что люди на фото согласны");
    expect(importSiteImages).not.toHaveBeenCalled();

    await userEvent.click(screen.getByLabelText(/У меня есть права использовать фото/));
    await userEvent.click(screen.getByRole("button", { name: "Добавить отмеченные" }));
    expect(importSiteImages).toHaveBeenCalledWith(["https://salon.example/photo.jpg"], true);
  });
});
