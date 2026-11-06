import { useLayoutEffect, useRef } from "react";
import type { FormEvent } from "react";
import { useStoreState } from "../store";
import type { State, Store } from "../store";
import "./Chat.css";

type Chip = { label: string; send?: string; primary?: boolean };

function chipsFor(s: State): Chip[] {
  if (s.pending) return [{ label: "Применить", primary: true }, { label: "Не надо" }];
  if (s.live) return [
    { label: "Как дела?" },
    { label: s.paused ? "Включи рекламу" : "Поставь на паузу", send: s.paused ? "Включи" : "Поставь на паузу" },
    { label: "Отмени последнее" },
    { label: "Что ты делал?" },
  ];
  if (s.stage === "start") {
    if (s.step === 0) return [{ label: "salon-primer.ru" }];
    if (s.step === 1) return [{ label: "Работаем до 22:00" }];
    if (s.step === 2) return [{ label: "20 000 ₽ в месяц", send: "20000" }];
    return [];
  }
  return [{ label: "Как дела?" }, { label: "Почему такая цена?" }, { label: "Не показывай по воскресеньям" }, { label: "Новая цена на маникюр 1400" }, { label: "Отмени последнее" }];
}

export function Chat({ store }: { store: Store }) {
  const s = useStoreState(store);
  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const lastId = s.msgs[s.msgs.length - 1]?.id;
  const chips = chipsFor(s);

  useLayoutEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [lastId]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    store.send(s.input);
    inputRef.current?.focus();
  };

  return (
    <section className="ah-chat" aria-label="Чат с ИИ">
      <div ref={logRef} className="ah-chat-log" role="log" aria-live="polite" aria-label="Сообщения" tabIndex={0}>
        <div className="ah-chat-msgs">
          {s.msgs.map((m) => (
            <div key={m.id} className={`ah-chat-msg ah-chat-msg-${m.who}`}>
              {m.text}
              {m.link && <button type="button" className="ah-btn ah-chat-link" onClick={() => store.goView(m.link!.view)}>{m.link.label}</button>}
            </div>
          ))}
        </div>
      </div>
      <div className="ah-chat-bottom">
        <div className="ah-chat-col">
          {chips.length > 0 && (
            <div className="ah-chat-chips" role="group" aria-label="Подсказки">
              {chips.map((c) => (
                <button key={c.label} type="button" className={c.primary ? "ah-btn ah-btn-primary" : "ah-btn"} onClick={() => store.send(c.send ?? c.label)}>{c.label}</button>
              ))}
            </div>
          )}
          <form className="ah-chat-form" onSubmit={submit}>
            <label className="ah-chat-sr" htmlFor="ah-chat-input">Сообщение для ИИ</label>
            <input
              ref={inputRef}
              id="ah-chat-input"
              className="ah-input ah-chat-input"
              autoComplete="off"
              value={s.input}
              onChange={(e) => store.set({ input: e.target.value })}
              placeholder={s.stage === "start" ? "Ссылка или пара слов о бизнесе" : "Что поменять?"}
            />
            <button type="submit" className="ah-btn ah-btn-primary ah-chat-send" aria-label="Отправить">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
            </button>
          </form>
        </div>
      </div>
    </section>
  );
}
