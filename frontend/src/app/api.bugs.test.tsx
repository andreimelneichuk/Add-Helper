import { formatErrorDetail } from "../api";
import { describe, expect, it } from "vitest";

describe("Ответы API: регрессии (BUG-…)", () => {
  describe("BUG-033: formatErrorDetail formats FastAPI/Pydantic validation errors", () => {
    it("33.1: extracts message from Pydantic array of validation errors", () => {
      const pydanticError = [
        { loc: ["body", "inn"], msg: "ИНН должен содержать 10 или 12 цифр", type: "value_error" },
      ];
      expect(formatErrorDetail(pydanticError)).toBe("ИНН должен содержать 10 или 12 цифр");
    });

    it("33.2: joins multiple error messages", () => {
      const pydanticErrors = [
        { loc: ["body", "inn"], msg: "Неверный ИНН" },
        { loc: ["body", "name"], msg: "Укажите имя" },
      ];
      expect(formatErrorDetail(pydanticErrors)).toBe("Неверный ИНН; Укажите имя");
    });

    it("33.3: leaves plain string message untouched", () => {
      expect(formatErrorDetail("Сайт недоступен")).toBe("Сайт недоступен");
    });
  });
});
