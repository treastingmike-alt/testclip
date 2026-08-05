import { createPortal } from "react-dom";

/* Shown once, after a payment has actually been fulfilled.
 *
 * The moment someone pays is the one moment they are certain they made a good
 * decision, and it was being wasted: checkout finished, Polar sent them back,
 * and the app looked exactly as it had before -- same balance, same everything,
 * no acknowledgement that money had changed hands. That silence reads as a
 * failed payment, and the first thing a buyer does about a failed payment is
 * ask for it back.
 *
 * So: confirm the charge landed, show the balance they can now see for
 * themselves, name what they just unlocked, and point at the one action they
 * came here to do. It is deliberately not a receipt -- Polar sends that.
 */

/* Entitlement key -> what it means to someone who just bought it. Keyed off the
   server's own entitlement list rather than a hardcoded plan description, so a
   plan that gains a capability advertises it here without a frontend change. */
const UNLOCKED = {
  multilingual: "Captions for speech that switches language mid-sentence",
  gameplay: "Gameplay split-screen layout",
  tighten_pauses: "Automatic dead-air removal",
  caption_editing: "Full caption editing — words, style, colour, position",
  branding: "Your own logo and handle on every clip",
  no_watermark: "No KlipCut watermark",
  share_pages: "Public share pages for any clip",
  priority: "Priority rendering",
};

export default function WelcomeUpgrade({ user, onClose, onStart }) {
  if (!user) return null;

  const planName = (user.plan || "creator").replace(/^./, (c) => c.toUpperCase());
  const credits = user.credits ?? 0;
  const clips = Math.floor(credits / 10);
  const perks = (user.entitlements || [])
    .map((key) => UNLOCKED[key])
    .filter(Boolean);

  return createPortal(
    <div className="upgrade-veil" onClick={onClose} data-testid="welcome-upgrade">
      <div className="upgrade-card welcome-card" onClick={(e) => e.stopPropagation()}
           role="dialog" aria-modal="true" aria-labelledby="welcome-title">
        <button className="upgrade-x" onClick={onClose} aria-label="Close">×</button>

        <span className="upgrade-eyebrow">Payment confirmed</span>
        <h3 id="welcome-title">You&rsquo;re on {planName}. 🎉</h3>

        {/* The balance first: it is the thing they just bought, and seeing the
            real number is what proves the payment actually landed. */}
        <p className="welcome-balance">
          <strong>{credits.toLocaleString()}</strong> credits
          <span> — about {clips.toLocaleString()} finished clips</span>
        </p>

        {perks.length > 0 && (
          <>
            <p className="upgrade-sub">Now unlocked for you:</p>
            <ul className="welcome-perks">
              {perks.map((perk) => (
                <li key={perk}>{perk}</li>
              ))}
            </ul>
          </>
        )}

        <div className="upgrade-actions">
          <button className="btn btn-ghost btn-sm" onClick={onClose}>
            Later
          </button>
          <button className="btn btn-primary btn-sm"
                  onClick={() => { onClose?.(); onStart?.(); }}>
            Make your first clip →
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
