import { useEffect, useRef } from "react";
import type { FormEvent } from "react";
import { rub, STEPS } from "../data";
import { useStoreState } from "../store";
import type { State, Store } from "../store";
import { adsLeft, innReady, STEP_BODIES } from "./start/Steps";
import "./Start.css";

function summary(s: State, real: boolean) {
  const site = s.site.trim();
  const links = [s.yandex, real ? s.marked : innReady(s), s.metrica].filter(Boolean).length;
  return [
    { label: "Сайт", value: site || "пока нет", on: !!site },
    { label: "Профиль", value: s.step > 1 ? "подтверждён" : s.step === 1 ? "на проверке" : "пока нет", on: s.step > 1 },
    { label: "Бюджет", value: s.step > 2 ? `${rub(Number(s.f.budget))} в месяц` : "пока нет", on: s.step > 2 },
    { label: "Объявления", value: s.step > 3 ? `${adsLeft(s)} готово` : "пока нет", on: s.step > 3 },
    { label: "Подключения", value: `${links} из 3`, on: links === 3 },
  ];
}

export function Start({ store }: { store: Store }) {
  const s = useStoreState(store);
  const base = STEPS[s.step] ?? STEPS[0]!;
  // В настоящем запуске сводка честно показывает, что готово: создание кампаний ещё не подключено.
  const step = store.reader && s.step === STEPS.length - 1
    ? { ...base, title: "Что готово к запуску", lead: "Сервис проверил всё, что нужно для запуска рекламы." }
    : store.writer && s.step === 3
      ? { ...base, lead: "Тексты пишет ИИ только по вашим фактам, код проверяет каждое. Изображения добавлю позже." }
      : base;
  const Body = STEP_BODIES[s.step] ?? STEP_BODIES[0]!;
  const title = useRef<HTMLHeadingElement>(null);
  const first = useRef(true);

  // При смене шага фокус и прокрутка возвращаются к заголовку.
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    title.current?.focus();
  }, [s.step]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    store.next();
  };

  return (
    <div className="st">
      <ol className="st-steps" aria-label="Шаги запуска">
        {STEPS.map((x, i) => (
          <li key={x.label} className={i <= s.step ? "is-on" : undefined} aria-current={i === s.step ? "step" : undefined}>
            <span className="st-step-label">{x.label}</span>
          </li>
        ))}
      </ol>
      <p className="st-step-now" aria-hidden="true">Шаг {s.step + 1} из {STEPS.length}: {step.label}</p>

      <div className="st-cols">
        <form className="ah-panel st-card" onSubmit={submit} noValidate>
          <h1 ref={title} tabIndex={-1} className="st-title">{step.title}</h1>
          <p className="st-lead">{step.lead}</p>
          <Body store={store} s={s} />
          <div className="st-actions">
            <button type="submit" className="ah-btn ah-btn-primary st-next">{store.reader && s.step === STEPS.length - 1 ? "Открыть кабинет" : step.cta}</button>
            {s.step > 0 && <button type="button" className="ah-btn" onClick={() => store.set({ step: s.step - 1, block: "" })}>Назад</button>}
            {s.block && <p role="alert" className="st-block">{s.block}</p>}
          </div>
          <button type="button" className="ah-btn st-hint" onClick={() => store.openChat()}>Можно спросить в чате</button>
        </form>

        <aside className="ah-panel st-side" aria-label="Что уже готово">
          <h2 className="ah-panel-title">Собирается</h2>
          {summary(s, !!store.reader).map((r) => (
            <div key={r.label} className="st-side-row">
              <i aria-hidden="true" className={r.on ? "is-on" : undefined} />
              <div>
                <div className="ah-muted">{r.label}</div>
                <div className="st-side-value">{r.value}</div>
              </div>
            </div>
          ))}
        </aside>
      </div>
    </div>
  );
}
