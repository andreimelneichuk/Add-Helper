// Шаги 4–8 мастера на настоящих данных: объявления, кабинет, маркировка, код статистики, сводка.
// Всё, что владелец вводит, сразу уходит в сервис; сводку готовности проверяет сервис.
import { useCallback, useEffect, useRef, useState } from "react";
import type { AdAccount, AdvertiserKind, LaunchAnswer, LaunchCheck, MarkingView, TagCheckView } from "../../../api";
import { type Ad, rub } from "../../data";
import type { State, Store } from "../../store";

type Props = { store: Store; s: State };

const say = (e: unknown) => (e instanceof Error ? e.message : "Сервис не ответил. Попробуйте позже.");
const KIND_LABEL: Record<AdvertiserKind, string> = { legal: "Организация", ip: "ИП", person: "Физическое лицо" };

export function RealYandex({ store, s }: Props) {
  const api = store.wizard;
  const [accounts, setAccounts] = useState<AdAccount[] | null>(null);
  const [status, setStatus] = useState("");
  const [manualLink, setManualLink] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [loginInput, setLoginInput] = useState("");
  const [askLogin, setAskLogin] = useState(false);
  const [busy, setBusy] = useState(false);
  const [tokenError, setTokenError] = useState<string | null>(null);

  useEffect(() => {
    if (!api) return;
    api.accounts().then((found) => {
      setAccounts(found);
      const isConn = found.some((a) => a.state === "connected");
      store.set({ yandex: isConn });
    }, (e: unknown) => setStatus(say(e)));
    if (api.manualTokenLink) {
      api.manualTokenLink().then((r) => setManualLink(r.url), () => setManualLink(null));
    }
  }, [api, store]);

  const connected = accounts?.find((a) => a.state === "connected");
  const isConnected = !!connected;
  const login = connected?.login || s.account?.email?.split("@")[0] || "Яндекс Директ";

  const saveToken = async () => {
    if (!api || !api.saveManualToken) return;
    setBusy(true);
    setTokenError(null);
    try {
      const saved = await api.saveManualToken(token.trim(), loginInput.trim() || undefined);
      setAccounts((all) => [...(all ?? []).filter((x) => x.login !== saved.login), saved]);
      store.set({ yandex: true, block: "" });
      setToken("");
      setLoginInput("");
      setAskLogin(false);
    } catch (e) {
      setAskLogin(/логин/i.test(say(e)));
      setTokenError(say(e));
    } finally {
      setBusy(false);
    }
  };

  const connectOAuth = () => {
    if (!api) return;
    setStatus("");
    api.connect("start").then((r) => store.navigate(r.authorize_url), (e: unknown) => setStatus(say(e)));
  };

  return (
    <div className="st-link">
      <div className="st-link-text">
        <div className="st-link-title">{isConnected ? `Кабинет подключён: ${login}` : "Кабинет Директа"}</div>
        <div className="ah-muted">
          {s.account?.email ? `Вы вошли как ${s.account.email}. ` : ""}
          {isConnected
            ? "Директ подключён. Кампании запуск создаст в этом кабинете."
            : "Чтобы сервис мог управлять рекламой в вашем Директе, предоставьте доступ к кабинету через токен доступа."}
        </div>
        {status && <p className="st-letter" role="status">{status}</p>}
      </div>
      {isConnected ? (
        <button type="button" className="ah-btn st-link-done" disabled>Подключено</button>
      ) : (
        <div className="st-token-box" style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 10, width: "100%" }}>
          <p className="set-hint" style={{ margin: 0 }}>
            Получите код доступа в Яндексе: нажмите кнопку, разрешите доступ, скопируйте строку доступа из адресной строки и вставьте сюда.
          </p>
          {manualLink && (
            <div>
              <button
                type="button"
                className="ah-btn"
                onClick={() => window.open(manualLink, "_blank", "noopener")}
              >
                Получить код доступа к Директу
              </button>
            </div>
          )}
          <label className="st-field" style={{ margin: 0 }}>
            <span className="ah-label">Токен доступа к Директу</span>
            <input
              type="password"
              className="ah-input"
              value={token}
              placeholder="Вставьте токен доступа из адресной строки"
              onChange={(e) => setToken(e.target.value)}
              autoComplete="off"
            />
          </label>
          {askLogin && (
            <label className="st-field" style={{ margin: 0 }}>
              <span className="ah-label">Логин в Директе</span>
              <input
                className="ah-input"
                value={loginInput}
                onChange={(e) => setLoginInput(e.target.value)}
                autoComplete="off"
              />
            </label>
          )}
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button
              type="button"
              className="ah-btn ah-btn-primary"
              disabled={busy || !token.trim()}
              onClick={saveToken}
            >
              {busy ? "Проверяю…" : "Сохранить токен"}
            </button>
            <button
              type="button"
              className="ah-btn"
              onClick={connectOAuth}
            >
              Подключить кабинет
            </button>
          </div>
          {tokenError && <p className="ah-error" role="alert" style={{ margin: 0 }}>{tokenError}</p>}
        </div>
      )}
    </div>
  );
}

export function RealMarking({ store }: Props) {
  const api = store.wizard;
  const [saved, setSaved] = useState<MarkingView | null>(null);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState("");
  const activeCheckRef = useRef(0);

  const check = useCallback(() => {
    if (!api) return;
    const checkId = ++activeCheckRef.current;
    setLoading(true);
    setStatus("");
    api.marking().then(
      (r) => {
        if (checkId !== activeCheckRef.current) return;
        setSaved(r.saved);
        store.set({ marked: !!(r.saved && r.saved.inn), block: "" });
        setLoading(false);
      },
      (e: unknown) => {
        if (checkId !== activeCheckRef.current) return;
        setStatus(say(e));
        setLoading(false);
      }
    );
  }, [api, store]);

  useEffect(() => {
    check();
    return () => {
      activeCheckRef.current++;
    };
  }, [check]);

  const directUrl = "https://direct.yandex.ru/registered/main.cmd?cmd=clientInfo";

  if (loading && !saved) {
    return <p className="ah-muted">Проверяю реквизиты в Яндекс Директе…</p>;
  }

  if (saved && saved.inn) {
    const kindTitle = KIND_LABEL[saved.kind] ?? saved.kind;
    return (
      <div className="st-link">
        <div className="st-link-text">
          <div className="st-link-title">Реквизиты указаны в Директе: ИНН {saved.inn}</div>
          <div className="ah-muted">
            {saved.name ? `${saved.name}. ` : ""}{kindTitle}. Яндекс автоматически передаст данные в ОРД при запуске рекламы по 347-ФЗ.
          </div>
          {status && <p className="st-letter" role="status">{status}</p>}
        </div>
        <div className="st-link-actions" style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button type="button" className="ah-btn st-link-done" disabled>Проверено</button>
          <button type="button" className="ah-btn" onClick={check} disabled={loading}>Проверить снова</button>
        </div>
      </div>
    );
  }

  return (
    <div className="st-link">
      <div className="st-link-text">
        <div className="st-link-title">Реквизиты рекламодателя для маркировки</div>
        <div className="ah-muted">
          По закону № 347-ФЗ вся интернет-реклама должна маркироваться. Яндекс Директ маркирует её автоматически через свой ОРД, но в вашем кабинете Директа реквизиты (ИНН) пока не указаны.
          <br /><br />
          Перейдите в настройки кабинета Яндекса, укажите данные плательщика/рекламодателя и нажмите «Проверить снова».
        </div>
        {status && <p className="st-letter" role="status">{status}</p>}
      </div>
      <div className="st-link-actions" style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
        <button
          type="button"
          className="ah-btn ah-btn-primary"
          onClick={() => window.open(directUrl, "_blank", "noopener")}
        >
          Перейти в Яндекс Директ
        </button>
        <button type="button" className="ah-btn" onClick={check} disabled={loading}>
          {loading ? "Проверяю…" : "Проверить снова"}
        </button>
      </div>
    </div>
  );
}

const TAG_TEXT = {
  found: (n: number[]) => `Код найден на сайте, счётчик ${n.join(", ")}. Что визиты доходят, проверю, когда подключите кабинет.`,
  missing: "На главной странице кода Метрики нет. Если код стоит через Google Tag Manager, я его не вижу: проверю по визитам после подключения кабинета.",
  failed: "Сайт не открылся при проверке. Попробуйте ещё раз чуть позже.",
  waiting: "Проверяю сайт, обычно до минуты.",
};

export function RealCode({ store, s }: Props) {
  const api = store.wizard;
  const [view, setView] = useState<TagCheckView | null>(null);
  const [status, setStatus] = useState("");
  const check = view?.check && view.check.url === view.site ? view.check : null;
  const waiting = check?.status === "queued" || check?.status === "running";
  useEffect(() => {
    if (!api) return;
    api.tagCheck().then(setView, (e: unknown) => setStatus(say(e)));
  }, [api]);
  // Пока проверка в очереди, спрашиваем сервис снова: воркер берёт её раз в минуту.
  useEffect(() => {
    if (!api || !waiting) return;
    const timer = window.setTimeout(() => { api.tagCheck().then(setView, (e: unknown) => setStatus(say(e))); }, store.waitMs);
    return () => window.clearTimeout(timer);
  }, [api, waiting, view, store.waitMs]);
  useEffect(() => { store.set({ metrica: !!view && (!view.site || check?.status === "found") }); }, [view, check, store]);
  if (!view) return <p className="ah-muted">{status || "Загружаю…"}</p>;
  if (!view.site) {
    return <p className="ah-muted">В профиле нет сайта. Обращения будут видны по звонкам и заявкам из Яндекса. Сайт можно добавить в настройках, тогда проверю код.</p>;
  }
  const letter = `Здравствуйте! Поставьте, пожалуйста, на сайт ${view.site} код счётчика Яндекс Метрики на все страницы. Без него реклама не увидит заявки и звонки.`;
  const run = () => {
    setStatus("");
    api?.requestTagCheck().then(setView, (e: unknown) => setStatus(say(e)));
  };
  const copy = () => { void navigator.clipboard?.writeText(letter).catch(() => undefined); store.set({ codeCopied: true }); };
  const found = check?.status === "found";
  return (
    <div className="st-link">
      <div className="st-link-text">
        <div className="st-link-title">Код статистики на {view.site}</div>
        <div className="ah-muted" role="status">
          {check ? (waiting ? TAG_TEXT.waiting : check.status === "found" ? TAG_TEXT.found(check.counters) : TAG_TEXT[check.status as "missing" | "failed"]) : "Поставьте код сами или перешлите письмо разработчику, а потом проверьте установку."}
        </div>
        {status && <p className="st-letter">{status}</p>}
        {s.codeCopied && !found && <p className="st-letter">{letter}</p>}
      </div>
      <div className="st-link-actions">
        <button type="button" className={found ? "ah-btn st-link-done" : "ah-btn ah-btn-primary"} disabled={waiting} onClick={run}>{found ? "Проверить снова" : "Проверить установку"}</button>
        {!found && <button type="button" className="ah-btn" onClick={copy}>{s.codeCopied ? "Письмо скопировано" : "Скопировать письмо разработчику"}</button>}
      </div>
    </div>
  );
}

export function RealLaunch({ store }: Props) {
  const api = store.wizard;
  const [check, setCheck] = useState<LaunchCheck | null>(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<LaunchAnswer | null>(null);
  const [problem, setProblem] = useState("");
  useEffect(() => {
    if (!api) return;
    api.launchCheck().then(setCheck, (e: unknown) => setStatus(say(e)));
  }, [api]);
  if (!check) return <p className="ah-muted">{status || "Проверяю, всё ли готово…"}</p>;
  const start = async () => {
    if (!api || busy) return;
    setBusy(true);
    setProblem("");
    try {
      setAnswer(await api.startLaunch());
      setCheck(await api.launchCheck());
    } catch (e) {
      setProblem(say(e));
    } finally {
      setBusy(false);
    }
  };
  const done = check.launched === "done";
  return (
    <>
      <ul className="st-ready" aria-label="Готовность к запуску">
        {check.items.map((item) => (
          <li key={item.key} className={item.ok ? "is-ok" : "is-missing"}>
            <span role="img" aria-label={item.ok ? "готово" : "не готово"}>{item.ok ? "✓" : "•"}</span> {item.text}
          </li>
        ))}
      </ul>
      {answer && answer.campaigns.length > 0 && (
        <ul className="st-ready" aria-label="Созданные кампании">
          {answer.campaigns.map((c) => (
            <li key={c.name} className="is-ok">
              <span role="img" aria-label="создана">✓</span> {c.name}: лимит {rub(c.limit_rubles)} на месяц
            </li>
          ))}
        </ul>
      )}
      <p className="st-letter" role="note">
        {done ? "" : check.ready ? "Всё нужное для запуска есть. " : "Чего не хватает, можно дополнить в настройках кабинета. "}
        {check.launch_text}
      </p>
      {problem && <p className="ah-error" role="alert">{problem}</p>}
      {check.launch_available && (
        <button type="button" className="ah-btn ah-btn-primary" disabled={busy} onClick={() => void start()}>
          {busy ? "Запускаю…" : "Запустить рекламу"}
        </button>
      )}
    </>
  );
}

/** Шаг «Объявления»: пишет настоящий писатель по фактам черновика, владелец оставляет нужные. */
export function RealAds({ store, s }: Props) {
  const chosenSaved = !!s.account;
  const [adding, setAdding] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newText, setNewText] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editText, setEditText] = useState("");

  const handleAdd = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTitle.trim() || !newText.trim()) return;
    const customAd: Ad = {
      id: `custom-${crypto.randomUUID()}`,
      name: newTitle.trim(),
      title: newTitle.trim(),
      hook: newTitle.trim(),
      text: newText.trim(),
      tag: "в рекламу",
      tagTone: "good",
      leads: 0,
      cost: 0,
      facts: "Ваш текст",
    };
    store.set({ ads: [customAd, ...s.ads], block: "" });
    setNewTitle("");
    setNewText("");
    setAdding(false);
  };

  const handleSaveEdit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingId || !editTitle.trim() || !editText.trim()) return;
    const updated = s.ads.map((item) =>
      item.id === editingId
        ? { ...item, title: editTitle.trim(), hook: editTitle.trim(), name: editTitle.trim(), text: editText.trim() }
        : item
    );
    store.set({ ads: updated, block: "" });
    setEditingId(null);
  };

  const handleDelete = (adId: string) => {
    if (s.ads.length <= 1) {
      store.set({ block: "Оставьте хотя бы одно объявление для запуска рекламы." });
      return;
    }
    const updated = s.ads.filter((item) => item.id !== adId);
    const nextDraftOff = { ...s.draftOff };
    delete nextDraftOff[adId];
    store.set({ ads: updated, draftOff: nextDraftOff, block: "" });
  };

  if (s.writing) {
    return (
      <div className="st-writing-wrap">
        <p className="ah-muted st-reading" role="status">
          Пишу объявления по вашим фактам и проверяю каждое: цены и обещания только из профиля. Обычно это меньше минуты.
        </p>
        <div className="st-ads">
          <div className="st-skeleton" />
          <div className="st-skeleton" />
        </div>
      </div>
    );
  }
  return (
    <>
      {s.adSet?.template && <p className="ah-muted">Писатель сейчас не ответил, поэтому объявления собраны по шаблону из ваших фактов. После запуска я буду писать новые и ставить лучшие.</p>}
      {s.adSet?.needsReview && <p className="ah-muted">У вашей сферы строгие правила рекламы: перед запуском каждое объявление покажу вам на подтверждение.</p>}
      {chosenSaved && s.ads.length > 0 && <p className="ah-muted">Выбор сохранён в сервисе: запуск начнётся с этих объявлений.</p>}
      {s.block && s.ads.length > 0 && <p className="ah-muted" role="alert" style={{ color: "var(--ah-bad, #e53935)" }}>{s.block}</p>}
      {s.ads.length === 0 && (
        <div className="st-link" style={{ marginBottom: 16 }}>
          <div className="st-link-text">
            <div className="st-link-title">Нет готовых объявлений</div>
            <div className="ah-muted" role="alert">
              {s.facts.some((f) => f.field === "service" || f.id === "prices")
                ? s.block || "Лимит генераций на сегодня исчерпан. Вы можете добавить своё объявление вручную или продолжить к кабинету."
                : "Объявления составляются по услугам из вашего профиля. Вернитесь на шаг «Профиль» и добавьте хотя бы одну услугу."}
            </div>
          </div>
          {s.facts.some((f) => f.field === "service" || f.id === "prices") ? (
            <button type="button" className="ah-btn ah-btn-primary" onClick={() => store.advance("Переходим к кабинету: объявления можно составить внутри настроек.")}>
              Продолжить к кабинету
            </button>
          ) : (
            <button type="button" className="ah-btn ah-btn-primary" onClick={() => store.set({ step: 1, block: "" })}>
              Вернуться в профиль
            </button>
          )}
        </div>
      )}

      {adding && (
        <form className="st-ad st-ad-plain" style={{ marginBottom: 16, border: "2px solid var(--ah-accent, #007aff)" }} onSubmit={handleAdd}>
          <div className="st-ad-body">
            <h3 style={{ margin: "0 0 8px 0" }}>Новое объявление</h3>
            <label style={{ display: "block", marginBottom: 8 }}>
              <span className="ah-muted" style={{ fontSize: 13 }}>Заголовок ({newTitle.length}/56)</span>
              <input
                type="text"
                className="ah-input"
                style={{ width: "100%", marginTop: 4 }}
                maxLength={56}
                value={newTitle}
                placeholder="Заголовок объявления"
                onChange={(e) => setNewTitle(e.target.value)}
                required
              />
            </label>
            <label style={{ display: "block", marginBottom: 12 }}>
              <span className="ah-muted" style={{ fontSize: 13 }}>Текст объявления ({newText.length}/81)</span>
              <textarea
                className="ah-input"
                rows={2}
                style={{ width: "100%", marginTop: 4 }}
                maxLength={81}
                value={newText}
                placeholder="Текст вашего объявления"
                onChange={(e) => setNewText(e.target.value)}
                required
              />
            </label>
            <div style={{ display: "flex", gap: 8 }}>
              <button type="submit" className="ah-btn ah-btn-primary" disabled={!newTitle.trim() || !newText.trim()}>Добавить</button>
              <button type="button" className="ah-btn" onClick={() => setAdding(false)}>Отмена</button>
            </div>
          </div>
        </form>
      )}

      <div className="st-ads">
        {s.ads.map((a) => {
          const off = !!s.draftOff[a.id];
          const action = off ? "Вернуть" : "Убрать";
          const isEditing = editingId === a.id;

          if (isEditing) {
            return (
              <form key={a.id} className="st-ad st-ad-plain" onSubmit={handleSaveEdit}>
                <div className="st-ad-body">
                  <h3 style={{ margin: "0 0 8px 0" }}>Редактирование объявления</h3>
                  <label style={{ display: "block", marginBottom: 8 }}>
                    <span className="ah-muted" style={{ fontSize: 13 }}>Заголовок ({editTitle.length}/56)</span>
                    <input
                      type="text"
                      className="ah-input"
                      style={{ width: "100%", marginTop: 4 }}
                      maxLength={56}
                      value={editTitle}
                      onChange={(e) => setEditTitle(e.target.value)}
                      required
                    />
                  </label>
                  <label style={{ display: "block", marginBottom: 12 }}>
                    <span className="ah-muted" style={{ fontSize: 13 }}>Текст ({editText.length}/81)</span>
                    <textarea
                      className="ah-input"
                      rows={2}
                      style={{ width: "100%", marginTop: 4 }}
                      maxLength={81}
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                      required
                    />
                  </label>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button type="submit" className="ah-btn ah-btn-primary" disabled={!editTitle.trim() || !editText.trim()}>Сохранить</button>
                    <button type="button" className="ah-btn" onClick={() => setEditingId(null)}>Отмена</button>
                  </div>
                </div>
              </form>
            );
          }

          return (
            <article key={a.id} className={off ? "st-ad st-ad-plain is-off" : "st-ad st-ad-plain"}>
              <div className="st-ad-body">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                  <h2 className="st-ad-title" style={{ margin: 0 }}>{a.title}</h2>
                  <span className={off ? "ah-tag ah-tag-neutral" : "ah-tag ah-tag-good"}>{off ? "Убрано" : "В рекламу"}</span>
                </div>
                <p className="st-ad-text">{a.text}</p>
                <p className="st-ad-facts">{a.facts}</p>
                {!chosenSaved && (
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                    <button type="button" className="ah-btn st-ad-toggle" aria-label={`${action} «${a.name}»`} onClick={() => store.set({ draftOff: { ...s.draftOff, [a.id]: !off }, block: "" })}>{action}</button>
                    <button type="button" className="ah-btn" onClick={() => { setEditingId(a.id); setEditTitle(a.title); setEditText(a.text); }}>Редактировать</button>
                    <button type="button" className="ah-btn" onClick={() => handleDelete(a.id)}>Удалить</button>
                  </div>
                )}
              </div>
            </article>
          );
        })}
      </div>
      {!chosenSaved && (
        <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
          <button type="button" className="ah-btn ah-btn-primary" onClick={() => setAdding(true)}>+ Своё объявление</button>
          <button type="button" className="ah-btn" onClick={() => void store.writeAds(true)}>{s.ads.length ? "Написать другие варианты" : "Написать ещё раз"}</button>
        </div>
      )}
    </>
  );
}
