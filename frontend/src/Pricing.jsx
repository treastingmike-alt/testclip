import { useEffect, useState } from "react";
import { getPlans, startCheckout } from "./api";

/**
 * Pricing section.
 *
 * Plans, tiers and prices all come from the backend, so the price on the card
 * and the price charged at checkout cannot drift apart. Paid plans carry a
 * credit dropdown: same plan, more credits per month, price follows.
 */
export default function Pricing({ user, onRequireAuth }) {
  const [data, setData] = useState(null);
  const [yearly, setYearly] = useState(true);
  const [chosen, setChosen] = useState({});   // plan id -> selected credit amount
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");

  /* Priced by the server, so the page cannot quote a rate the backend would
     not actually charge. The fallback only covers the first paint. */
  const perClip = data?.credits_per_clip ?? 10;

  useEffect(() => {
    getPlans()
      .then((d) => {
        setData(d);
        // Start each plan on its headline tier.
        setChosen(Object.fromEntries(d.plans.map((p) => [p.id, p.credits])));
      })
      .catch(() => {});
  }, []);

  async function choose(plan) {
    if (!user) return onRequireAuth?.();
    setBusy(plan.id);
    setNotice("");
    try {
      const r = await startCheckout({
        planId: plan.id,
        credits: chosen[plan.id],
        interval: yearly ? "yearly" : "monthly",
      });
      setNotice(
        r.status === "provider_not_configured"
          ? "Checkout isn't connected yet — payments are coming shortly."
          : "Redirecting…"
      );
    } catch (e) {
      setNotice(e.message);
    } finally {
      setBusy("");
    }
  }

  if (!data) return null;

  return (
    <section className="pricing" id="pricing">
      <div className="container">
        <header className="pricing-head">
          <span className="eyebrow">Pricing</span>
          <h2>
            Pay for what you <span className="serif">actually clip.</span>
          </h2>
          {/* The differentiator, stated plainly. Everyone else meters the
              video you upload; a three-hour stream costs three hours whether
              you keep two clips or none. Here the source is free and you pay
              only for finished clips. */}
          <p>
            {perClip} credits = 1 finished clip. Source length doesn't matter —
            a three-hour stream costs the same as a ten-minute one. You pay for
            clips you can post, not for our processing time.
          </p>

          <div className="billing-toggle" role="group" aria-label="Billing interval">
            <button
              type="button"
              className={!yearly ? "on" : ""}
              onClick={() => setYearly(false)}
              aria-pressed={!yearly}
            >
              Monthly
            </button>
            <button
              type="button"
              className={yearly ? "on" : ""}
              onClick={() => setYearly(true)}
              aria-pressed={yearly}
            >
              Yearly <span className="save">save 20%</span>
            </button>
          </div>
        </header>

        <div className="plan-grid">
          {data.plans.map((plan) => {
            const tiers = plan.tiers || [];
            const credits = chosen[plan.id] ?? plan.credits;
            const tier = tiers.find((t) => t.credits === credits);
            const price = tier
              ? (yearly ? tier.yearly_usd : tier.monthly_usd)
              : (yearly ? plan.yearly_usd : plan.monthly_usd);
            const current = user?.plan === plan.id;
            const free = plan.monthly_usd === 0;

            return (
              <div key={plan.id} className={`plan ${plan.popular ? "popular" : ""}`}>
                {plan.popular && <span className="plan-badge">Most popular</span>}
                <h3>{plan.name}</h3>
                <p className="plan-blurb">{plan.blurb}</p>

                <div className="plan-price">
                  <strong>${price}</strong>
                  <span>{free ? "" : "/month"}</span>
                </div>
                {yearly && price > 0 && (
                  <p className="plan-note">billed yearly · ${price * 12}/yr</p>
                )}

                {tiers.length > 0 ? (
                  <label className="credit-picker">
                    <select
                      value={credits}
                      onChange={(e) =>
                        setChosen({ ...chosen, [plan.id]: Number(e.target.value) })
                      }
                      aria-label={`Credits per month on ${plan.name}`}
                    >
                      {tiers.map((t) => (
                        <option key={t.credits} value={t.credits}>
                          {t.credits.toLocaleString()} credits/month
                        </option>
                      ))}
                    </select>
                    <span className="credit-hint">
                      {Math.floor(credits / perClip).toLocaleString()} clips a month
                    </span>
                  </label>
                ) : (
                  <p className="credit-static">
                    {Math.floor(plan.credits / perClip).toLocaleString()} free
                    clips on signup
                  </p>
                )}

                <button
                  className={`btn ${plan.popular ? "btn-primary" : "btn-ghost"} btn-block btn-sm`}
                  onClick={() => choose(plan)}
                  disabled={current || free || busy === plan.id}
                >
                  {current ? "Current plan"
                    : free ? "Included free"
                    : busy === plan.id ? "Opening…" : `Get ${plan.name}`}
                </button>

                <ul className="plan-features">
                  {plan.features.map((f) => (
                    <li key={f}>
                      <svg viewBox="0 0 24 24" width="15" height="15" fill="none" aria-hidden="true">
                        <path d="m5 13 4 4L19 7" stroke="currentColor" strokeWidth="2.6"
                              strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                      {f}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>

        <div className="topups">
          <h4>Run out mid-month?</h4>
          <p className="topups-sub">
            Top up without changing your plan — these never expire.
          </p>
          <div className="topup-row">
            {data.topups.map((t) => (
              <div className="topup" key={t.credits}>
                <strong>{Math.floor(t.credits / perClip).toLocaleString()}</strong>
                <span>clips</span>
                <em>${t.usd}</em>
              </div>
            ))}
          </div>
        </div>

        {notice && <p className="pricing-notice">{notice}</p>}
      </div>
    </section>
  );
}
