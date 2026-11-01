import { useEffect, useState } from "react";
import type { Api, Profile } from "../api";

export function ProfileScreen({ api }: { api: Api }) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  useEffect(() => {
    api.profile().then(setProfile, (e: Error) => setStatus(e.message));
  }, [api]);
  if (!profile) return <p className="cab-empty">{status ?? "Загружаем…"}</p>;

  const set = (patch: Partial<Profile>) => {
    setProfile({ ...profile, ...patch });
    setStatus(null);
  };
  const save = async () => {
    try {
      setProfile(await api.saveProfile({ ...profile, features: profile.features.filter((f) => f.trim()) }));
      setStatus("Сохранено. Новые объявления будут писаться по этим данным.");
    } catch (e) {
      setStatus((e as Error).message);
    }
  };

  return (
    <>
      <div className="cab-head">
        <h1>О бизнесе</h1>
        <p className="cab-muted cab-period">Только эти факты попадают в рекламу. Чего здесь нет — того в объявлениях не будет.</p>
      </div>
      <section className="cab-panel cab-form">
        <label className="cab-field">
          <span>Название</span>
          <input value={profile.name ?? ""} disabled />
        </label>
        <label className="cab-field">
          <span>Сайт</span>
          <input value={profile.website} onChange={(e) => set({ website: e.target.value })} placeholder="https://" />
        </label>
        <label className="cab-field">
          <span>Адрес</span>
          <input value={profile.address} onChange={(e) => set({ address: e.target.value })} />
        </label>
        <label className="cab-field">
          <span>Сфера</span>
          <input value={profile.niche} onChange={(e) => set({ niche: e.target.value })} placeholder="маникюр" />
        </label>

        <h2>Услуги и цены</h2>
        <table className="cab-table cab-services">
          <thead>
            <tr><th className="cab-left">Услуга</th><th>Цена, ₽</th><th /></tr>
          </thead>
          <tbody>
            {profile.services.map((service, i) => (
              <tr key={i}>
                <td className="cab-left">
                  <input
                    aria-label={`Услуга ${i + 1}`}
                    value={service.name}
                    onChange={(e) => set({ services: profile.services.map((s, j) => (j === i ? { ...s, name: e.target.value } : s)) })}
                  />
                </td>
                <td>
                  <input
                    aria-label={`Цена услуги ${i + 1}`}
                    inputMode="numeric"
                    value={service.price ?? ""}
                    onChange={(e) => {
                      const digits = e.target.value.replace(/\D/g, "");
                      set({ services: profile.services.map((s, j) => (j === i ? { ...s, price: digits ? Number(digits) : null } : s)) });
                    }}
                  />
                </td>
                <td>
                  <button type="button" className="cab-link" onClick={() => set({ services: profile.services.filter((_, j) => j !== i) })}>Убрать</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <button type="button" className="cab-btn" onClick={() => set({ services: [...profile.services, { name: "", price: null }] })}>Добавить услугу</button>

        <h2>Чем вы отличаетесь</h2>
        <p className="cab-muted">По одному факту в строке. Например: «Стерильные инструменты из крафт-пакета».</p>
        <textarea
          className="cab-textarea"
          rows={4}
          value={profile.features.join("\n")}
          onChange={(e) => set({ features: e.target.value.split("\n") })}
        />
        <div className="cab-actions">
          <button type="button" className="cab-btn cab-btn-primary" onClick={save}>Сохранить</button>
          {status && <span role="status" className="cab-muted">{status}</span>}
        </div>
      </section>
    </>
  );
}
