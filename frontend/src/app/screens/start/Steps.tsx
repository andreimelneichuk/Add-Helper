import { useState } from "react";
import type { ComponentType } from "react";
import { MIN_BUDGET, rub, STEP } from "../../data";
import { fixtures } from "../../fixtures";
import { isInn } from "../../schemas";
import type { Fact } from "../../data";
import { saveStartSite } from "../../draft";
import { AdArt, Compose } from "../AdDetail";
import { RealAds, RealCode, RealLaunch, RealMarking, RealYandex } from "./RealSteps";
import type { State, Store } from "../../store";

type StepProps = { store: Store; s: State };

const PRESETS = [15000, 20000, 25000];
// Сумму в день считает код: это деление, а не прогноз.
const perDay = (month: number) => `около ${rub(month / 30)} в день`;

export const adsLeft = (s: State) => s.ads.filter((a) => !s.draftOff[a.id]).length;
export const innReady = (s: State) => isInn(s.inn);

function AboutStep({ store, s }: StepProps) {
  const reading = s.reading !== null;
  const signed = s.account !== null;
  return (
    <>
      {!signed && (
        <div className="st-auth-box">
          <div className="st-auth-box-text">
            <strong>Уже есть кабинет?</strong>
            <span>Войдите через Яндекс, чтобы сразу открыть свой кабинет.</span>
          </div>
          <button type="button" className="ah-btn" onClick={store.loginDirect}>
            Войти через Яндекс
          </button>
        </div>
      )}
      {signed && (
        <div className="st-auth-box is-signed">
          <span style={{ fontSize: "13px" }}>Вы вошли как <strong>{s.account?.email}</strong></span>
        </div>
      )}
      <label className="st-field">
        <span className="ah-label">Ссылка на сайт или страницу</span>
        <input className="ah-input st-input-big" inputMode="url" autoComplete="url" value={s.site} placeholder="Например: ваш-салон.рф" readOnly={reading} onChange={(e) => { store.set({ site: e.target.value, block: "" }); saveStartSite(e.target.value); }} />
      </label>
      {reading && <p role="status" className="st-reading">Читаю сайт: главную и страницы с ценами и контактами. Обычно это занимает одну-две минуты.</p>}
      {store.reader ? (
        <>
          <p className="ah-muted">Регистрироваться пока не нужно. Сайта нет или он не читается? Заполните профиль сами.</p>
          <button type="button" className="ah-link st-by-hand" onClick={store.fillByHand}>Заполнить вручную</button>
        </>
      ) : (
        <p className="ah-muted">Сайта нет? Подойдёт соцсеть или карточка на картах, либо расскажите о бизнесе в чате. Регистрироваться пока не нужно.</p>
      )}
    </>
  );
}

const FACT_CATEGORIES: [NonNullable<Fact["field"]>, string][] = [
  ["service", "Услуга"],
  ["feature", "Особенность"],
  ["address", "Адрес"],
  ["niche", "Сфера"],
];

function FactEditor({ store, s, label, id }: StepProps & { label: string; id: string | null }) {
  const value = s.factEdit?.value ?? "";
  const existing = id !== null ? s.facts.find((f) => f.id === id) : null;
  const currentField: Fact["field"] = s.factEdit?.field ?? existing?.field ?? "service";
  const categoryTitle = FACT_CATEGORIES.find(([cat]) => cat === currentField)?.[1];
  const userChangedCategory = s.factEdit?.field !== undefined && s.factEdit.field !== existing?.field;
  const displayLabel = userChangedCategory ? categoryTitle ?? label : label;
  return (
    <div className="st-fact st-fact-editing">
      <div className="st-fact-types">
        {FACT_CATEGORIES.map(([cat, title]) => (
          <button
            key={cat}
            type="button"
            className={currentField === cat ? "ah-btn ah-btn-primary is-on" : "ah-btn"}
            onClick={() => store.set({ factEdit: { id, value, field: cat } })}
          >
            {title}
          </button>
        ))}
      </div>
      <label className="st-field">
        <span className="ah-label">{displayLabel}</span>
        {/* Шаг это форма: Enter здесь сохраняет факт, а не ведёт дальше. */}
        <input
          className="ah-input"
          autoFocus
          placeholder={currentField === "service" ? "Например, Стрижка, 1 500 ₽" : ""}
          value={value}
          onChange={(e) => store.set({ factEdit: { id, field: currentField, value: e.target.value } })}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              store.saveFact();
            }
          }}
        />
      </label>
      <div className="st-fact-actions">
        <button type="button" className="ah-btn ah-btn-primary" disabled={!value.trim()} onClick={store.saveFact}>Сохранить</button>
        <button type="button" className="ah-btn" onClick={() => store.set({ factEdit: null, block: "" })}>Отмена</button>
        {id !== null && <button type="button" className="ah-btn st-fact-remove" onClick={() => store.removeFact(id)}>Убрать</button>}
      </div>
    </div>
  );
}

function ProfileStep({ store, s }: StepProps) {
  const adding = s.factEdit?.id === null;
  const hasService = s.facts.some((f) => f.field === "service" || f.id === "prices");
  return (
    <>
      {!hasService && (
        <p className="st-need-service-hint" role="note">
          Добавьте хотя бы одну услугу: объявления пишутся по вашим услугам.
        </p>
      )}
      <div className="st-grid-2">
        {s.facts.map((f) => (s.factEdit?.id === f.id ? <FactEditor key={f.id} store={store} s={s} label={f.label} id={f.id} /> : (
          <div key={f.id} className="st-fact">
            <div className="st-fact-body">
              <div className="st-fact-head"><span>{f.label}</span><span>{f.source}</span></div>
              <div className="st-fact-value">{f.value}</div>
              {f.quote && <blockquote className="st-fact-quote">«{f.quote}»</blockquote>}
            </div>
            <button type="button" className="ah-btn st-fact-edit" aria-label={`Изменить: ${f.label}`} onClick={() => store.set({ factEdit: { id: f.id, value: f.value, field: f.field }, block: "" })}>Изменить</button>
          </div>
        )))}
        {adding && <FactEditor store={store} s={s} label="Новый факт" id={null} />}
      </div>
      {!adding && (
        <button type="button" className="ah-btn st-fact-add" onClick={() => store.set({ factEdit: { id: null, value: "" }, block: "" })}>
          Добавить факт
        </button>
      )}
    </>
  );
}

function BudgetStep({ store, s }: StepProps) {
  const custom = s.budgetDraft !== null;
  const typed = Number(s.budgetDraft ?? 0);
  const min = s.budgetOptions?.min ?? MIN_BUDGET;
  const max = s.budgetOptions?.max ?? Infinity;
  return (
    <>
      <div className="st-budgets">
        {(s.budgetOptions?.presets ?? PRESETS).map((value) => (
          <button key={value} type="button" className="st-budget" aria-pressed={!custom && s.f.budget === value} onClick={() => store.set({ f: { ...s.f, budget: value }, budgetDraft: null, block: "" })}>
            <strong>{rub(value)}</strong>
            <span>{perDay(value)}</span>
          </button>
        ))}
      </div>
      <label className={custom ? "st-budget-own is-on" : "st-budget-own"}>
        <span className="ah-label">Своя сумма в месяц, ₽</span>
        <input className="ah-input" inputMode="numeric" autoComplete="off" placeholder={`От ${rub(min)}`} value={s.budgetDraft ?? ""} onChange={(e) => {
          const draft = e.target.value.replace(/\D/g, "").slice(0, 8);
          const n = Number(draft);
          const presets = s.budgetOptions?.presets ?? PRESETS;
          const defaultPreset = presets[0] ?? MIN_BUDGET;
          store.set({
            budgetDraft: draft || null,
            block: "",
            ...(!draft ? { f: { ...s.f, budget: defaultPreset } } : n >= min && n <= max ? { f: { ...s.f, budget: n } } : {}),
          });
        }} />
        {custom && <span className="ah-muted">{typed >= min ? perDay(typed) : `Минимум ${rub(min)} в месяц: от 50 ₽ в день по правилам Яндекса плюс 10% резерва`}</span>}
      </label>
      <p className="ah-muted">Прогноз обращений покажу, когда подключите кабинет: его считает Яндекс по вашему району и услугам. Больше выбранной суммы не потрачу: лимит будет стоять в самом Яндексе.</p>
    </>
  );
}

const MODES: { id: "watch" | "confirm"; name: string; hint: string }[] = [
  { id: "watch", name: "Смотрит", hint: "Только показывает, что бы я сделал. Ничего не меняю." },
  { id: "confirm", name: "Предлагает", hint: "Предлагаю действия, вы подтверждаете каждое." },
];

function AutopilotStep({ store, s }: StepProps) {
  const [check, setCheck] = useState("");
  const [note, setNote] = useState("");
  const ap = s.autopilot;
  const set = (patch: Partial<typeof ap>) => store.set({ autopilot: { ...ap, ...patch }, block: "" });
  const estimate = async () => {
    const n = Number(check);
    if (!Number.isInteger(n) || n < 1) { setNote("Укажите средний чек в рублях."); return; }
    if (n > 1_000_000) { setNote("Чек больше миллиона рублей я посчитать не могу, введите цену обращения сами."); return; }
    try {
      const r = await store.reader?.economicsEstimate?.(n);
      if (r?.goal_rubles) { if (ap.goal) setNote(`${r.how} Вашу цифру не меняю.`); else { set({ goal: String(r.goal_rubles) }); setNote(r.how); } } else setNote("Не удалось посчитать, введите сумму сами.");
    } catch { setNote("Не удалось посчитать, введите сумму сами."); }
  };
  return (
    <>
      <div className="st-budgets" role="radiogroup" aria-label="Как работать с рекламой">
        {MODES.map((m) => (
          <button key={m.id} type="button" role="radio" aria-checked={ap.mode === m.id} className="st-budget" onClick={() => set({ mode: m.id })}>
            <strong>{m.name}</strong>
            <span>{m.hint}</span>
          </button>
        ))}
      </div>
      <p className="ah-muted">Режим «Сам» включается отдельно, когда накопится история работы.</p>
      <div className="st-budgets" role="radiogroup" aria-label="Как часто смотреть">
        {([["daily", "Каждый день"], ["weekly", "Раз в неделю"]] as const).map(([id, name]) => (
          <button key={id} type="button" role="radio" aria-checked={ap.cadence === id} className="st-budget" onClick={() => set({ cadence: id })}>
            <strong>{name}</strong>
          </button>
        ))}
      </div>
      <label className="st-budget-own">
        <span className="ah-label">Сколько готовы платить за одно обращение, ₽</span>
        <input className="ah-input" inputMode="numeric" autoComplete="off" value={ap.goal} onChange={(e) => set({ goal: e.target.value.replace(/\D/g, "").slice(0, 7) })} />
      </label>
      <label className="st-budget-own">
        <span className="ah-label">Или назовите средний чек, подскажу, ₽</span>
        <input className="ah-input" inputMode="numeric" autoComplete="off" value={check} onChange={(e) => setCheck(e.target.value.replace(/\D/g, "").slice(0, 7))} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void estimate(); } }} />
      </label>
      <button type="button" className="ah-btn" onClick={estimate}>Подсказать</button>
      {note && <p className="ah-muted">{note}</p>}
      <p className="ah-muted">Без этой цифры мне не с чем сравнивать результат, поэтому шаг без неё не пройти.</p>
    </>
  );
}

function AdsStep({ store, s }: StepProps) {
  if (store.writer) return <RealAds store={store} s={s} />;
  return <MockAds store={store} s={s} />;
}

function MockAds({ store, s }: StepProps) {
  const [editing, setEditing] = useState<string | null>(null);
  const target = s.ads.find((a) => a.id === editing);
  return (
    <>
    <div className="st-ads">
      {s.ads.map((a) => {
        const off = !!s.draftOff[a.id];
        const action = off ? "Вернуть" : "Убрать";
        return (
          <article key={a.id} className={off ? "st-ad is-off" : "st-ad"}>
            <AdArt ad={a} hook={a.hook} />
            <div className="st-ad-body">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                <h2 className="st-ad-title" style={{ margin: 0 }}>{a.title}</h2>
                <span className={off ? "ah-tag ah-tag-neutral" : "ah-tag ah-tag-good"}>{off ? "Убрано" : "В рекламу"}</span>
              </div>
              <p className="st-ad-text">{a.text}</p>
              <p className="st-ad-facts">{a.facts}</p>
              <button type="button" className="ah-btn st-ad-toggle" aria-label={`${action} «${a.name}»`} onClick={() => store.set({ draftOff: { ...s.draftOff, [a.id]: !off }, block: "" })}>{action}</button>
              <button type="button" className="ah-btn st-ad-toggle" aria-label={`Переделать «${a.name}»`} onClick={() => setEditing(a.id)}>Переделать</button>
            </div>
          </article>
        );
      })}
    </div>
    {target && (
      <Compose key={target.id} s={s} base={target} current="Сейчас" useLabel="Взять этот вариант" onCancel={() => setEditing(null)} onUse={(ad) => { store.set({ ads: s.ads.map((a) => (a.id === ad.id ? ad : a)) }); setEditing(null); }} />
    )}
    </>
  );
}

function YandexStep({ store, s }: StepProps) {
  if (store.reader && s.account && store.wizard) return <RealYandex store={store} s={s} />;
  if (store.reader) {
    const signed = s.account !== null;
    // Вошли раньше и черновик ещё в браузере: «Продолжить» сохранит его новым сайтом.
    const unsaved = store.transferDeps !== null && !store.keptExisting;
    return (
      <div className="st-link">
        <div className="st-link-text">
          <div className="st-link-title">{signed ? "Вход выполнен" : "Вход через Яндекс"}</div>
          <div className="ah-muted">
            {signed
              ? `${s.account?.email ? `Вы вошли: ${s.account.email}. ` : ""}${unsaved
                ? "Нажмите «Продолжить»: сохраню профиль и бюджет как новый сайт, затем подключим к нему кабинет Директа."
                : "Профиль и бюджет сохранены в сервисе. Кабинет Директа подключите в настройках: ИНН, код статистики и запуск появятся, когда Яндекс откроет доступ к API."}`
              : "Войдите тем логином, где будет реклама. Профиль и бюджет сохранятся в сервисе, ничего вводить заново не придётся."}
          </div>
        </div>
        {!signed && <button type="button" className="ah-btn ah-btn-primary" onClick={store.login}>Войти через Яндекс</button>}
      </div>
    );
  }
  const signed = s.account !== null;
  const login = s.account?.email?.split("@")[0] || "salon-primer";
  return (
    <div className="st-link">
      <div className="st-link-text">
        <div className="st-link-title">Кабинет Яндекса</div>
        <div className="ah-muted">
          {signed
            ? `Вы вошли: ${s.account?.email}. Профиль и бюджет сохранены в сервисе. Кабинет Директа подключён вместе с аккаунтом Яндекса.`
            : `Подключён: логин ${login}. Кабинет Директа подключён вместе с аккаунтом Яндекса.`}
        </div>
      </div>
      <button
        type="button"
        className="ah-btn st-link-done"
        onClick={() => {
          store.set({ yandex: true, block: "" });
          if (!signed) store.login();
        }}
      >
        Подключено
      </button>
    </div>
  );
}

function InnStep({ store, s }: StepProps) {
  if (store.reader) return <RealMarking store={store} s={s} />;
  return (
    <label className="st-field st-inn">
      <span className="ah-label">ИНН для маркировки</span>
      <input className="ah-input" inputMode="numeric" autoComplete="off" maxLength={12} value={s.inn} placeholder="10 или 12 цифр" onChange={(e) => store.set({ inn: e.target.value.replace(/\D/g, ""), block: "" })} />
    </label>
  );
}

const CODE_LETTER = "Здравствуйте! Поставьте, пожалуйста, на сайт salon-primer.ru код статистики Яндекс Метрики на все страницы. Без него реклама не увидит заявки и звонки.";

function CodeStep({ store, s }: StepProps) {
  if (store.reader) return <RealCode store={store} s={s} />;
  // Буфер обмена может быть недоступен, поэтому текст письма показываем и на экране.
  const copy = () => { void navigator.clipboard?.writeText(CODE_LETTER).catch(() => undefined); store.set({ codeCopied: true }); };
  return (
    <div className="st-link">
      <div className="st-link-text">
        <div className="st-link-title">Код статистики на сайте</div>
        <div className="ah-muted">{s.metrica ? "Код найден на сайте, обращения считаются" : "Поставьте код сами или перешлите письмо разработчику, а потом проверьте установку."}</div>
        {s.codeCopied && <p className="st-letter">{CODE_LETTER}</p>}
      </div>
      <div className="st-link-actions">
        <button type="button" className={s.metrica ? "ah-btn st-link-done" : "ah-btn ah-btn-primary"} onClick={() => store.set({ metrica: true, block: "" })}>{s.metrica ? "Проверено" : "Проверить установку"}</button>
        {!s.metrica && <button type="button" className="ah-btn" onClick={copy}>{s.codeCopied ? "Письмо скопировано" : "Скопировать письмо разработчику"}</button>}
      </div>
    </div>
  );
}

function LaunchStep({ store, s }: StepProps) {
  if (store.reader) return <RealLaunch store={store} s={s} />;
  const rows = [
    { label: "Бюджет", value: `${rub(Number(s.f.budget))} в месяц` },
    { label: "Где покажем", value: "Поиск и сайты Яндекса" },
    { label: "Кому", value: fixtures.TEXT.audience },
    { label: "Объявления", value: String(adsLeft(s)) },
    { label: "На счёте в Яндексе", value: rub(fixtures.ACCOUNT.running) },
  ];
  return (
    <dl className="st-grid-2 st-rows" aria-label="Итоговая сводка">
      {rows.map((r) => (
        <div key={r.label} className="st-fact">
          <dt className="st-fact-head">{r.label}</dt>
          <dd className="st-fact-value">{r.value}</dd>
        </div>
      ))}
    </dl>
  );
}

const BODIES: Record<keyof typeof STEP, ComponentType<StepProps>> = {
  site: AboutStep,
  profile: ProfileStep,
  budget: BudgetStep,
  autopilot: AutopilotStep,
  ads: AdsStep,
  cabinet: YandexStep,
  marking: InnStep,
  stats: CodeStep,
  launch: LaunchStep,
};
/** Экраны шагов в порядке `STEP`: пропустить шаг в списке нельзя, не сойдётся тип. */
export const STEP_BODIES: ComponentType<StepProps>[] = (Object.keys(STEP) as (keyof typeof STEP)[]).map((name) => BODIES[name]!);
