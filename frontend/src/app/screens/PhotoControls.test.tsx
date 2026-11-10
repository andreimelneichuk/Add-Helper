import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Api } from "../../api";
import type { Ad } from "../data";
import { PhotoControls } from "./AdDetail";

afterEach(cleanup);

const ad = { id: "acc/9", photoId: 7 } as unknown as Ad;

describe("фото работающего объявления", () => {
  it("показывает и свои, и сделанные сервисом фото без растяжения", async () => {
    const photos = vi.fn(async () => ({
      photos: [
        { id: 7, width: 600, height: 4000, url: "/p/7", source: "owner" as const },
        { id: 8, width: 1024, height: 1024, url: "/p/8", source: "generated" as const },
      ],
      max: 5,
    }));
    render(<PhotoControls api={{ photos } as unknown as Api} ad={ad} disabled={false} />);

    const own = await screen.findByAltText("Фото бизнеса 1");
    expect(photos).toHaveBeenCalledWith(undefined, true);
    expect(screen.getByAltText("Фото от сервиса 2")).toBeInTheDocument();
    // Размеры из файла не задают высоту: плитка квадратная по стилю.
    expect(own).not.toHaveAttribute("height");
  });

  it("делает фото по услуге и кладёт в список, не меняя объявление само", async () => {
    const photos = vi.fn(async () => ({ photos: [], max: 5 }));
    const generatePhoto = vi.fn(async () => ({ id: 21, url: "/p/21", source: "generated" as const }));
    const changeAdPhoto = vi.fn();
    render(<PhotoControls api={{ photos, generatePhoto, changeAdPhoto } as unknown as Api} ad={ad} services={["Стрижка"]} disabled={false} />);
    await screen.findByText(/Пока нет фото/);

    await userEvent.click(screen.getByRole("button", { name: "Сделать фото" }));

    expect(generatePhoto).toHaveBeenCalledWith("Стрижка");
    expect(await screen.findByAltText("Фото от сервиса 1")).toHaveAttribute("src", "/p/21");
    expect(changeAdPhoto).not.toHaveBeenCalled();
  });
});
