import { createPortal } from "react-dom";

/* The upgrade prompt.
 *
 * A dialog rather than a jump to the pricing section: someone hits this in the
 * middle of setting up a clip, and throwing them to the bottom of the marketing
 * page loses the work they were doing. It names the ONE thing they just reached
 * for, then offers the whole list -- a generic "upgrade!" modal teaches nobody
 * what they would be buying.
 */

const UNLOCKS = [
  ["gameplay", "Gameplay split-screen", "Pairs your clip with looping footage down the frame"],
  ["tighten_pauses", "Pause tightening", "Cuts the dead air so a clip feels edited, not trimmed"],
  ["multilingual", "Mixed-language captions", "Follows speech that switches language mid-sentence"],
  ["no_watermark", "No watermark", "Free clips carry a small credit at the foot of the frame"],
  ["share_pages", "Share pages", "Publish a clip with its score breakdown on its own page"],
  ["branding", "Your own logo", "Burn a handle or logo into every clip"],
  ["priority", "Priority rendering", "Your jobs go ahead of the free queue"],
];

const REASONS = {
  clips: "More clips per video",
  gameplay: "Gameplay split-screen",
  tighten_pauses: "Pause tightening",
  multilingual: "Mixed-language captions",
  share_pages: "Share pages",
  no_watermark: "Watermark-free clips",
  branding: "Custom branding",
  length: "Longer source videos",
  upload: "Bigger uploads",
};

export default function UpgradeModal({ reason, limits, onClose, onSeePlans }) {
  return createPortal(
    <div className="upgrade-veil" onClick={onClose} data-testid="upgrade-modal">
      <div className="upgrade-card" onClick={(e) => e.stopPropagation()}
           role="dialog" aria-modal="true" aria-labelledby="upgrade-title">
        <button className="upgrade-x" onClick={onClose} aria-label="Close"
                data-testid="upgrade-close">×</button>

        <span className="upgrade-eyebrow">Creator plan</span>
        <h3 id="upgrade-title">
          {reason && REASONS[reason]
            ? <>{REASONS[reason]} is part of Creator.</>
            : <>Room to actually post every day.</>}
        </h3>
        <p className="upgrade-sub">
          Free gives you {limits?.max_clips ?? 2} clips per video on sources up to{" "}
          {limits?.max_source_minutes ?? 30} minutes. Creator lifts all of that and
          turns on the parts of the editor that do the most work.
        </p>

        <ul className="upgrade-list">
          {UNLOCKS.map(([id, name, note]) => (
            <li key={id} className={reason === id ? "hit" : ""}>
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" aria-hidden="true">
                <path d="m5 13 4 4L19 7" stroke="currentColor" strokeWidth="2.6"
                      strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <span>
                <strong>{name}</strong>
                <em>{note}</em>
              </span>
            </li>
          ))}
        </ul>

        <div className="upgrade-actions">
          <button className="btn btn-ghost btn-sm" onClick={onClose}
                  data-testid="upgrade-later">
            Keep the free plan
          </button>
          <button className="btn btn-primary btn-shine" onClick={onSeePlans}
                  data-testid="upgrade-see-plans">
            See plans and pricing
          </button>
        </div>
        <p className="upgrade-foot">Nothing changes on your account until you choose a plan.</p>
      </div>
    </div>,
    document.body,
  );
}
