import { describe, expect, it, vi } from "vitest";
import { confirmText } from "./store";
import { fakeApi, open } from "./testLive";

const draft = { id: "w1", headline: "Маникюр 1200 ₽", text: "Запишитесь", facts: [] };

async function store(publishWizardAd: ReturnType<typeof vi.fn>) {
  const wizardAds = vi.fn().mockResolvedValue({ ads: [draft] });
  return open(fakeApi({ wizardAds, publishWizardAd } as never));
}

describe("публикация объявления с фото", () => {
  it("отправляет выбранное фото вместе с группировкой", async () => {
    const publish = vi.fn(async () => ({ status: "applied" as const, group: "Маникюр", text: null }));
    const s = await store(publish);

    await s.run({ action: "adPublish", value: "w1", grouping: "services", imageId: 21 });

    expect(publish).toHaveBeenCalledWith("w1", "services", 21);
  });

  it("без выбора фото объявление уходит одним текстом", async () => {
    const publish = vi.fn(async () => ({ status: "applied" as const, group: "Общие услуги", text: null }));
    const s = await store(publish);

    await s.run({ action: "adPublish", value: "w1", grouping: "general", imageId: null });

    expect(publish).toHaveBeenCalledWith("w1", "general", null);
  });

  it("переспрашивает владельца и называет, с фото или без", async () => {
    const s = await store(vi.fn());

    const withPhoto = confirmText(s.state, { action: "adPublish", value: "w1", grouping: "general", imageId: 5 });
    const without = confirmText(s.state, { action: "adPublish", value: "w1", grouping: "general", imageId: null });

    expect(withPhoto).toContain("С выбранным фото");
    expect(without).toContain("Без фото");
  });

  it("показывает фото, записанное в черновике, и берёт его для предпросмотра", async () => {
    const wizardAds = vi.fn().mockResolvedValue({ ads: [{ ...draft, image_id: 21 }] });
    const s = await open(fakeApi({ wizardAds } as never));

    const ad = s.state.ads.find((x) => x.id === "w1");
    expect(ad?.photoId).toBe(21);
    expect(ad?.photo).toBe("/api/businesses/b1/photos/21");
  });
});
