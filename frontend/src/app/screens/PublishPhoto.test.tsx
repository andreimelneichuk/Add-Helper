import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Api } from "../../api";
import { PublishPhoto } from "./AdDetail";

afterEach(cleanup);

function Harness({ api, onPick }: { api: Api; onPick: (id: number | null) => void }) {
  const [value, setValue] = useState<number | null>(null);
  return <PublishPhoto api={api} services={["Маникюр", "Педикюр"]} value={value} onChange={(id) => { setValue(id); onPick(id); }} disabled={false} />;
}

const own = { id: 7, width: 800, height: 800, url: "/p/7", source: "owner" as const };

describe("фото для нового объявления", () => {
  it("по умолчанию без фото, своё фото выбирается и снимается", async () => {
    const photos = vi.fn(async () => ({ photos: [own], max: 5 }));
    const onPick = vi.fn();
    render(<Harness api={{ photos } as unknown as Api} onPick={onPick} />);

    expect(await screen.findByAltText("Своё фото 1")).toBeInTheDocument();
    expect(photos).toHaveBeenCalledWith(undefined, true);
    expect(screen.getByRole("button", { name: "Без фото" })).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(screen.getByRole("button", { name: "Взять своё фото" }));
    expect(onPick).toHaveBeenLastCalledWith(7);
    await userEvent.click(screen.getByRole("button", { name: "Без фото" }));
    expect(onPick).toHaveBeenLastCalledWith(null);
  });

  it("делает фото по выбранной услуге и сразу выбирает его", async () => {
    const photos = vi.fn(async () => ({ photos: [], max: 5 }));
    const generatePhoto = vi.fn(async () => ({ id: 21, url: "/p/21", source: "generated" as const }));
    const onPick = vi.fn();
    render(<Harness api={{ photos, generatePhoto } as unknown as Api} onPick={onPick} />);
    await screen.findByRole("button", { name: "Без фото" });

    await userEvent.click(screen.getByRole("button", { name: "Педикюр" }));
    await userEvent.click(screen.getByRole("button", { name: "Сделать фото" }));

    expect(generatePhoto).toHaveBeenCalledWith("Педикюр");
    expect(await screen.findByAltText("Фото от сервиса 1")).toBeInTheDocument();
    expect(onPick).toHaveBeenLastCalledWith(21);
  });

  it("называет причину отказа словами сервиса и ничего не выбирает", async () => {
    const photos = vi.fn(async () => ({ photos: [], max: 5 }));
    const generatePhoto = vi.fn(async () => { throw new Error("Сегодня уже сделано 3 фото."); });
    const onPick = vi.fn();
    render(<Harness api={{ photos, generatePhoto } as unknown as Api} onPick={onPick} />);
    await screen.findByRole("button", { name: "Без фото" });

    await userEvent.click(screen.getByRole("button", { name: "Сделать фото" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Сегодня уже сделано 3 фото.");
    expect(onPick).not.toHaveBeenCalled();
  });

  it("загружает своё фото только после отметки согласия", async () => {
    const photos = vi.fn(async () => ({ photos: [], max: 5 }));
    const uploadPhoto = vi.fn(async () => ({ id: 33, url: "/p/33" }));
    const onPick = vi.fn();
    const { container } = render(<Harness api={{ photos, uploadPhoto } as unknown as Api} onPick={onPick} />);
    await screen.findByRole("button", { name: "Без фото" });

    const file = new File(["x"], "salon.jpg", { type: "image/jpeg" });
    await userEvent.upload(container.querySelector("input[type=file]") as HTMLInputElement, file);
    expect(screen.getByRole("button", { name: "Добавить фото" })).toBeDisabled();
    await userEvent.click(screen.getByLabelText(/На фото нет людей/));
    await userEvent.click(screen.getByRole("button", { name: "Добавить фото" }));

    expect(uploadPhoto).toHaveBeenCalledWith(file, true);
    expect(onPick).toHaveBeenLastCalledWith(33);
  });
});
