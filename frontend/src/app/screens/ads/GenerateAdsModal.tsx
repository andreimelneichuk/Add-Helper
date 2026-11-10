import { useEffect, useState } from "react";
import { createGuestApi, type Photo } from "../../../api";
import type { Ad } from "../../data";
import { liveApi, type LiveStore } from "../../live";
import { usePollingTask } from "../../polling";
import { useStoreState, type Store } from "../../store";

type GenerateAdsModalProps = {
  isOpen: boolean;
  onClose: () => void;
  store: Store;
};

const NO_SERVICES = "В профиле нет услуг. Заполните профиль в настройках: объявления пишутся только по его фактам.";

type GeneratedItem = {
  headline: string;
  text: string;
  facts: string[];
  services?: string[];
};

export function GenerateAdsModal({ isOpen, onClose, store }: GenerateAdsModalProps) {
  const s = useStoreState(store);
  const api = liveApi(store);

  // Извлекаем список услуг из профиля
  const profileServices: string[] = s.live?.rawProfile?.services
    ? s.live.rawProfile.services.map((x) => x.name.trim()).filter(Boolean)
    : s.live?.profile
        ?.find(([key]: [string, string]) => key === "Услуги и цены")?.[1]
        ?.split(";")
        ?.map((item: string) => item.trim().replace(/,\s*\d.*$/u, ""))
        ?.filter(Boolean) ?? [];

  // В кабинете услуги только из профиля: выдуманная услуга стала бы фактом, которого нет у бизнеса.
  const live = api !== null;
  const noServices = live && profileServices.length === 0;
  const serviceOptions: string[] = live
    ? (profileServices.length > 1 ? ["Все услуги", ...profileServices] : profileServices)
    : ["Все услуги", "Стрижка", "Маникюр", "Акция"];

  const [selectedService, setSelectedService] = useState(serviceOptions[0] ?? "Все услуги");
  const [customService, setCustomService] = useState("");
  const [wish, setWish] = useState("");
  const [variants, setVariants] = useState(3);
  const [busy, setBusy] = useState(false);
  const [statusText, setStatusText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [generated, setGenerated] = useState<GeneratedItem[] | null>(null);
  const [selectedIndices, setSelectedIndices] = useState<number[]>([0, 1]);
  // Фото к черновикам: номер черновика → номер фото; null — без фото.
  const [photoIds, setPhotoIds] = useState<Record<number, number | null>>({});
  const [photoList, setPhotoList] = useState<Photo[]>([]);
  const [photoBusy, setPhotoBusy] = useState("");
  const [photoNote, setPhotoNote] = useState("");
  const [adSetMeta, setAdSetMeta] = useState<{ id: number; token: string } | null>(null);

  // Окно закрыли: опрос набора и его запрос прекращаем, чтобы не спрашивать сервис впустую.
  const poller = usePollingTask();
  useEffect(() => {
    if (!isOpen) poller.cancel();
  }, [isOpen, poller]);

  if (!isOpen) return null;

  /** Фото к каждому объявлению по смыслу: модель называет тему фото, выбирает код. Сцену рисуем
   *  только для услуги, к которой подходящего фото нет, и не больше одной на услугу. */
  const assignPhotos = async (live: NonNullable<typeof api>, ads: GeneratedItem[]) => {
    setPhotoBusy("Смотрю фото и подбираю к каждому объявлению подходящее…");
    setPhotoNote("");
    setPhotoIds({});
    try {
      const { matches, photos } = await live.matchPhotos(ads.map((ad) => ad.services ?? []));
      let list = photos;
      setPhotoList(list);
      const ids: Record<number, number | null> = Object.fromEntries(matches.map((m, i) => [i, m.image_id]));
      setPhotoIds({ ...ids });
      const missing = [...new Set(matches.flatMap((m) => (m.image_id === null && m.missing ? [m.missing] : [])))];
      for (const service of missing) {
        setPhotoBusy(`Для «${service}» подходящего фото нет. Рисую сцену, это занимает до минуты…`);
        try {
          const made = await live.generatePhoto(service);
          list = [...list.filter((p) => p.id !== made.id), { id: made.id, url: made.url, width: 1024, height: 1024, source: "generated" }];
          matches.forEach((m, i) => { if (m.image_id === null && m.missing === service) ids[i] = made.id; });
          setPhotoList(list);
          setPhotoIds({ ...ids });
        } catch (e: unknown) {
          setPhotoNote(`${e instanceof Error ? e.message : "Не получилось сделать фото."} Остальные объявления можно добавить без фото или выбрать фото вручную.`);
          break;
        }
      }
      setPhotoList(list);
    } catch (e: unknown) {
      setPhotoNote(`${e instanceof Error ? e.message : "Не удалось подобрать фото."} Объявления можно добавить без фото.`);
    } finally {
      setPhotoBusy("");
    }
  };

  const nextPhoto = (index: number) => {
    const choices: (number | null)[] = [null, ...photoList.map((p) => p.id)];
    const at = choices.indexOf(photoIds[index] ?? null);
    setPhotoIds((prev) => ({ ...prev, [index]: choices[(at + 1) % choices.length] ?? null }));
  };

  /** Набор по профилю бизнеса: никаких запасных шаблонов, при сбое только причина. */
  const generateLive = async (live: NonNullable<typeof api>) => {
    try {
      const profile = await live.profile();
      const named = profile.services.filter((x) => x.name.trim());
      const services = selectedService === "Все услуги" ? named : named.filter((x) => x.name.trim() === selectedService);
      if (services.length === 0) throw new Error(NO_SERVICES);
      const guest = createGuestApi();
      const queued = await guest.writeAds({
        niche: profile.niche,
        address: profile.address,
        services: services.map((x) => ({ name: x.name, price: x.price })),
        features: profile.features,
        variants,
      });
      setAdSetMeta({ id: queued.id, token: queued.token });
      const polled = await poller.run({
        task: (signal) => guest.adSet(queued.id, queued.token, { signal }),
        isDone: (set) => set.status === "failed" || set.status === "ready",
      });
      if (polled.status === "aborted") return;
      if (polled.status === "timeout") throw new Error("Объявления пишутся дольше обычного. Попробуйте позже.");
      const set = polled.value;
      if (set.status === "failed") throw new Error(set.problem_text ?? "Не получилось написать объявления. Попробуйте ещё раз.");
      if (!set.ads || set.ads.length === 0) throw new Error("По фактам профиля не получилось написать объявления. Добавьте услуги или особенности в настройках.");
      setGenerated(set.ads);
      setSelectedIndices(set.ads.map((_, i) => i));
      setStatusText("");
      void assignPhotos(live, set.ads);
      return;
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Не получилось написать объявления. Попробуйте ещё раз.");
      setStatusText("");
    } finally {
      setBusy(false);
    }
  };

  const handleGenerate = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setStatusText("ИИ пишет варианты и проверяет факты по вашему профилю…");

    if (api) {
      await generateLive(api);
      return;
    }
    const targetService = selectedService === "Своя услуга" ? customService.trim() || "Услуга" : selectedService;
    const features = wish.trim() ? [wish.trim()] : [];

    try {
      const guest = createGuestApi();
      const queued = await guest.writeAds({
        niche: targetService,
        address: "наш салон",
        services: [{ name: targetService, price: null }],
        features,
        variants,
      });

      setAdSetMeta({ id: queued.id, token: queued.token });

      // Опрашиваем воркер
      const polled = await poller.run({
        task: (signal) => guest.adSet(queued.id, queued.token, { signal }),
        isDone: (set) => set.status === "failed" || (set.status === "ready" && !!set.ads && set.ads.length > 0),
      });
      if (polled.status === "aborted") {
        setBusy(false);
        return;
      }
      if (polled.status === "timeout") throw new Error("Генерация занимает дольше обычного.");
      const set = polled.value;
      if (set.status === "failed") {
        throw new Error(set.problem_text ?? "Не получилось написать объявления. Попробуйте ещё раз.");
      }
      setGenerated(set.ads ?? []);
      setSelectedIndices((set.ads ?? []).map((_, i) => i));
      setStatusText("");
      setBusy(false);
      return;
    } catch (err: unknown) {
      // Шаблон вместо писателя не подставляем: реклама говорит только то, что подтверждено профилем.
      setError(err instanceof Error ? err.message : "Не получилось написать объявления. Попробуйте ещё раз.");
      setStatusText("");
      setBusy(false);
    }
  };

  const handleSaveToAds = async () => {
    if (!generated || selectedIndices.length === 0) return;
    setBusy(true);

    try {
      if (api) {
        // В кабинете объявления живут на сервере: показываем их только после того, как сервис их принял.
        if (!adSetMeta) throw new Error("Набор объявлений не найден. Напишите варианты ещё раз.");
        const photos = Object.fromEntries(selectedIndices.flatMap((i) => (photoIds[i] ? [[i, photoIds[i]!]] : [])));
        await api.adoptAds({ id: adSetMeta.id, token: adSetMeta.token, kept: selectedIndices, ...(Object.keys(photos).length ? { photos } : {}) });
        const n = selectedIndices.length;
        store.set({ notice: `Добавил ${n} ${n === 1 ? "объявление" : "объявления"} к запуску.` });
        await (store as LiveStore).refresh();
        onClose();
        return;
      }

      // Добавляем объявления в локальное состояние store
      const newAds: Ad[] = selectedIndices.map((idx) => {
        const item = generated[idx]!;
        return {
          id: `gen-${Date.now()}-${idx}`,
          name: item.headline,
          tag: "готово к запуску",
          tagTone: "accent",
          leads: 0,
          cost: 0,
          hook: item.headline,
          title: item.headline,
          text: item.text,
          facts: item.facts.join(", "),
        };
      });

      store.set({
        ads: [...newAds, ...s.ads],
        notice: `Добавлено ${newAds.length} ${newAds.length === 1 ? "новое объявление" : "новых объявления"} в запуск!`,
      });

      if (store instanceof Object && "refresh" in store && typeof (store as LiveStore).refresh === "function") {
        void (store as LiveStore).refresh();
      }

      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить объявления");
    } finally {
      setBusy(false);
    }
  };

  const toggleSelect = (i: number) => {
    setSelectedIndices((prev) => (prev.includes(i) ? prev.filter((x) => x !== i) : [...prev, i]));
  };

  return (
    <div className="ah-modal-overlay" role="dialog" aria-modal="true" aria-labelledby="gen-ann-title">
      <div className="ah-modal">
        <div className="ah-modal-head">
          <div>
            <h2 id="gen-ann-title" className="ah-modal-title">Сгенерировать новые объявления</h2>
            <p className="ah-muted" style={{ marginTop: 4 }}>
              ИИ составит рекламные тексты по фактам из профиля бизнеса.
            </p>
          </div>
          <button type="button" className="ah-modal-close" aria-label="Закрыть" onClick={onClose} disabled={busy}>
            ✕
          </button>
        </div>

        {!generated ? (
          noServices ? (
            <div className="ah-modal-body">
              <p role="alert">{NO_SERVICES}</p>
              <div className="ah-modal-foot">
                <button type="button" className="ah-btn" onClick={onClose}>Закрыть</button>
                <button type="button" className="ah-btn ah-btn-primary" onClick={() => { onClose(); store.goView("settings"); }}>Открыть настройки</button>
              </div>
            </div>
          ) : (
          <form onSubmit={handleGenerate} className="ah-modal-body">
            <div>
              <span className="ah-label">Какая услуга или направление</span>
              <div className="ah-chips" style={{ marginTop: 6, marginBottom: 8 }}>
                {serviceOptions.map((opt) => (
                  <button
                    key={opt}
                    type="button"
                    className="ah-chip"
                    aria-pressed={selectedService === opt}
                    onClick={() => setSelectedService(opt)}
                  >
                    {opt}
                  </button>
                ))}
                {!live && <button
                  type="button"
                  className="ah-chip"
                  aria-pressed={selectedService === "Своя услуга"}
                  onClick={() => setSelectedService("Своя услуга")}
                >
                  + Своя услуга
                </button>}
              </div>

              {selectedService === "Своя услуга" && (
                <input
                  className="ah-input"
                  style={{ width: "100%", marginTop: 8 }}
                  placeholder="Например: Пилинг лица или Мужская стрижка"
                  value={customService}
                  onChange={(e) => setCustomService(e.target.value)}
                  autoFocus
                />
              )}
            </div>

            <label>
              <span className="ah-label">Сколько вариантов написать</span>
              <input
                className="ah-input"
                type="number"
                min={1}
                max={10}
                value={variants}
                onChange={(e) => setVariants(Math.min(10, Math.max(1, Number(e.target.value) || 1)))}
                style={{ width: 120, marginTop: 6 }}
              />
              <span className="ah-muted" style={{ fontSize: "12px", display: "block", marginTop: 4 }}>
                В одной группе Direct можно запустить до 3 вариантов. Остальные останутся черновиками.
              </span>
              <span className="ah-muted" style={{ fontSize: "12px", display: "block", marginTop: 4 }}>
                Direct сначала ротирует объявления, затем чаще показывает вариант с более высоким CTR по мере накопления статистики.
              </span>
            </label>

            {live ? (
              <p className="ah-muted">
                ИИ пишет только по фактам профиля: услугам, ценам, адресу и особенностям. Чтобы добавить акцию, впишите её в профиль в настройках.
              </p>
            ) : (
            <div>
              <label htmlFor="gen-wish" className="ah-label">
                Пожелания к объявлениям (своими словами)
              </label>
              <textarea
                id="gen-wish"
                className="ah-input"
                style={{ width: "100%", minHeight: 80, padding: "10px 14px", resize: "vertical" }}
                placeholder="Например: Сделай акцент на скидке 20% на первое посещение в ноябре, напиши кратко и с призывом записаться."
                value={wish}
                onChange={(e) => setWish(e.target.value)}
              />
              <span className="ah-muted" style={{ fontSize: "12px", display: "block", marginTop: 4 }}>
                Объявления составляются строго по правилам закона о рекламе и фактам вашего бизнеса.
              </span>
            </div>
            )}

            {statusText && (
              <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--accent)", fontWeight: 500 }}>
                <span className="ah-spin" aria-hidden="true">⏳</span>
                <span>{statusText}</span>
              </div>
            )}

            {error && <p className="ah-error" role="alert">{error}</p>}

            <div className="ah-modal-foot">
              <button type="button" className="ah-btn" onClick={onClose} disabled={busy}>
                Отмена
              </button>
              <button type="submit" className="ah-btn ah-btn-primary" disabled={busy}>
                {busy ? "Сочиняю…" : "Сгенерировать через ИИ"}
              </button>
            </div>
          </form>
          )
        ) : (
          <div className="ah-modal-body">
            <p className="ah-muted">
              Вот варианты, которые написал ИИ. Отметьте те, которые хотите отправить в показы:
            </p>
            {live && photoBusy && <p className="ah-muted" role="status">{photoBusy}</p>}
            {live && photoNote && <p className="ah-muted" role="status">{photoNote}</p>}

            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {generated.map((item, idx) => {
                const checked = selectedIndices.includes(idx);
                return (
                  <article
                    key={idx}
                    onClick={() => toggleSelect(idx)}
                    style={{
                      padding: "14px 16px",
                      borderRadius: "14px",
                      border: checked ? "2px solid var(--accent)" : "1px solid var(--line-strong)",
                      background: checked ? "var(--accent-soft)" : "var(--canvas)",
                      cursor: "pointer",
                      transition: "all 0.15s",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                      <strong style={{ fontSize: "15px", color: "var(--ink)" }}>{item.headline}</strong>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleSelect(idx)}
                        style={{ width: 18, height: 18, accentColor: "var(--accent)", cursor: "pointer" }}
                      />
                    </div>
                    {live && photoIds[idx] && (
                      <img
                        src={photoList.find((p) => p.id === photoIds[idx])?.url}
                        alt={`Фото к варианту ${idx + 1}`}
                        style={{ width: 96, height: 96, objectFit: "cover", borderRadius: 10, marginBottom: 8, display: "block" }}
                      />
                    )}
                    <p style={{ margin: 0, fontSize: "14px", color: "var(--ink)" }}>{item.text}</p>
                    {live && photoList.length > 0 && !photoBusy && (
                      <button type="button" className="ah-btn" style={{ marginTop: 8 }} onClick={(e) => { e.stopPropagation(); nextPhoto(idx); }}>
                        {photoIds[idx] ? "Другое фото" : "Добавить фото"}
                      </button>
                    )}
                    {item.facts.length > 0 && (
                      <span className="ah-muted" style={{ fontSize: "12px", display: "block", marginTop: 6 }}>
                        Факты: {item.facts.join(", ")}
                      </span>
                    )}
                  </article>
                );
              })}
            </div>

            {error && <p className="ah-error" role="alert">{error}</p>}

            <div className="ah-modal-foot">
              <button
                type="button"
                className="ah-btn"
                onClick={() => { setGenerated(null); setError(null); }}
                disabled={busy}
              >
                ← Изменить запрос
              </button>
              <button
                type="button"
                className="ah-btn ah-btn-primary"
                onClick={handleSaveToAds}
                disabled={busy || !!photoBusy || selectedIndices.length === 0}
              >
                {busy ? "Сохраняю…" : `Добавить выбранные (${selectedIndices.length})`}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
