import { expect, it } from "vitest";
import en from "@/messages/en.json";
import de from "@/messages/de.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import pt from "@/messages/pt-BR.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";

it.each(Object.entries({ en, de, es, fr, pt, ja, ko, zh }))(
  "image copy is complete in %s",
  (_locale, messages) => {
    const copy = messages.nvke.installImage;
    expect(Object.keys(copy).sort()).toEqual(Object.keys(en.nvke.installImage).sort());
    for (const value of Object.values(copy)) expect(value.trim().length).toBeGreaterThan(0);
  }
);
