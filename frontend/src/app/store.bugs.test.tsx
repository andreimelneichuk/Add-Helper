import { draftAd, initialState } from "./store";
import { describe, expect, it } from "vitest";

describe("Магазин состояния: регрессии (BUG-…)", () => {
  describe("BUG-018: askRefusal in store.ts supports user-added promo facts without f.id === 'promo'", () => {
    it("18.1: allows discount request when user added a promo fact with custom id", () => {
      const s = initialState();
      s.facts = [
        { id: "what", field: "niche", label: "Чем занимаетесь", value: "Маникюр", source: "с сайта" },
        { id: "where", field: "address", label: "Где", value: "Москва", source: "с сайта" },
        { id: "hours", field: "feature", label: "Когда", value: "Каждый день", source: "с сайта" },
        // User added a promo fact!
        { id: "n1", field: "feature", label: "Акция", value: "Скидка 20% на первый визит", source: "от вас" },
      ];

      // Request an ad about the promo/discount
      const result = draftAd(s, null, "акция");
      // Fixed: Generates promo ad successfully, no refusal
      expect(result).toHaveProperty("ad");
      if ("ad" in result) {
        expect(result.ad.hook).toContain("первый визит");
      }
    });
  });
});
