import { useEffect, useRef } from "react";
import type { FormEvent } from "react";
import { createApi, createBusiness, openSession } from "../../api";
import { rub, STEP, STEPS } from "../data";
import { useStoreState } from "../store";
import type { State, Store } from "../store";
import { adsLeft, innReady, STEP_BODIES } from "./start/Steps";
import "./Start.css";

export function formatSite(url: string): string {
  let s = url.trim();
  try {
    s = decodeURI(s);
  } catch {
    // ignore malformed URI
  }
  return s
    .replace(/^https?:\/\//i, "")
    .replace(/^www\./i, "")
    .replace(/\/+$/, "");
}

function summary(s: State, real: boolean) {
  const site = s.site.trim();
  const siteDisplay = site ? formatSite(site) : "пока нет";
  const links = [s.yandex, real ? s.marked : (s.marked || innReady(s)), s.metrica].filter(Boolean).length;
  const adsCount = adsLeft(s);
  const adsOn = adsCount > 0;
  return [
    { label: "Сайт", value: siteDisplay, title: site || undefined, on: !!site },
    { label: "Профиль", value: s.step > STEP.profile ? "подтверждён" : s.step === STEP.profile ? "на проверке" : "пока нет", on: s.step > STEP.profile },
    { label: "Бюджет", value: s.step > STEP.budget ? `${rub(Number(s.f.budget))} в месяц` : "пока нет", on: s.step > STEP.budget },
    { label: "Объявления", value: adsOn ? `${adsCount} готово` : "пока нет", on: adsOn },
    { label: "Подключения", value: `${links} из 3`, on: links === 3 },
  ];
}

export function Start({ store }: { store: Store }) {
  const s = useStoreState(store);
  const base = STEPS[s.step] ?? STEPS[STEP.site]!;
  // В настоящем запуске сводка честно показывает, что готово: создание кампаний ещё не подключено.
  const step = store.reader && s.step === STEPS.length - 1
    ? { ...base, title: "Что готово к запуску", lead: "Сервис проверил всё, что нужно для запуска рекламы." }
    : store.writer && s.step === STEP.ads
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

  const isBusy = s.step === STEP.site && s.reading !== null;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (isBusy) return;
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
            {!(s.step === STEP.ads && s.ads.length === 0 && !s.writing) && (
              <button type="submit" className="ah-btn ah-btn-primary st-next" disabled={isBusy}>{store.reader && s.step === STEPS.length - 1 ? "Открыть кабинет" : step.cta}</button>
            )}
            {s.step > STEP.site && <button type="button" className="ah-btn" onClick={() => store.set({ step: s.step - 1, block: "" })}>Назад</button>}
            {s.block && !(store.writer && s.step === STEP.ads && s.ads.length === 0 && !s.writing) && (
              <span className="st-block-wrap">
                <span role="alert" className="st-block">{s.block}</span>
                {s.step === STEP.ads && (
                  <button type="button" className="ah-btn st-block-action" onClick={() => store.set({ step: STEP.profile, block: "" })}>
                    К услугам
                  </button>
                )}
                {s.step >= STEP.cabinet && s.block.includes("У вас уже есть бизнес") && (
                  <button
                    type="button"
                    className="ah-btn ah-btn-primary st-block-action"
                    onClick={() => {
                      const deps = { session: openSession, createBusiness, business: createApi, open: createApi };
                      void store.createAsNew(deps);
                    }}
                  >
                    Создать как новый проект
                  </button>
                )}
              </span>
            )}
          </div>
        </form>

        <aside className="ah-panel st-side" aria-label="Что уже готово">
          <h2 className="ah-panel-title">Собирается</h2>
          {summary(s, !!store.reader).map((r) => (
            <div key={r.label} className="st-side-row">
              <i aria-hidden="true" className={r.on ? "is-on" : undefined} />
              <div>
                <div className="ah-muted">{r.label}</div>
                <div className="st-side-value" title={r.title}>{r.value}</div>
              </div>
            </div>
          ))}
        </aside>
      </div>
    </div>
  );
}
