import { createPortal } from "react-dom";

/* The upgrade prompt.
 *
 * A dialog rather than a jump to the pricing section: someone hits this in the
 * middle of setting up a clip, and throwing them to the bottom of the marketing
 * page loses the work they were doing. It names the ONE thing they just reached
 * for, then offers the whole list -- a generic "upgrade!" modal teaches nobody
 * what they would be buying.
 */

const COPY = {
  length: {
    title: "Upgrade to process this video.",
    body: (limits) => `Free supports videos up to ${limits?.max_source_minutes ?? 15} minutes. Creator supports longer videos and includes 100 finished clips each month.`,
  },
  upload: {
    title: "Upgrade to upload this file.",
    body: () => "Creator gives you more upload room and includes 100 finished clips each month.",
  },
  clips: {
    title: "Upgrade to make more clips.",
    body: (limits) => `Free includes up to ${limits?.max_clips ?? 2} clips from one video. Creator includes 100 finished clips each month.`,
  },
  gameplay: { title: "Upgrade for gameplay split-screen.", body: () => "Unlock this layout plus 100 finished clips each month with Creator." },
  tighten_pauses: { title: "Upgrade to tighten pauses automatically.", body: () => "Remove dead air automatically and get 100 finished clips each month with Creator." },
  multilingual: { title: "Upgrade for mixed-language captions.", body: () => "Caption speech that switches languages and get 100 finished clips each month with Creator." },
  share_pages: { title: "Upgrade to publish a share page.", body: () => "Publish clips on their own pages and get 100 finished clips each month with Creator." },
  no_watermark: { title: "Upgrade for watermark-free clips.", body: () => "Remove the KlipCut credit and get 100 finished clips each month with Creator." },
  branding: { title: "Upgrade to add your branding.", body: () => "Add your logo or handle and get 100 finished clips each month with Creator." },
  caption_editing: { title: "Upgrade to edit captions.", body: () => "Change caption words, style, colour, size and position, plus get 100 finished clips each month with Creator." },
  editor_export: { title: "Upgrade to export more edits.", body: () => "Free includes one edited export. Creator gives you unlimited edited exports plus 100 finished clips each month." },
};

export default function UpgradeModal({ reason, limits, onClose, onSeePlans }) {
  const copy = COPY[reason] || {
    title: "Upgrade to unlock this option.",
    body: () => "Creator includes 100 finished clips each month.",
  };
  return createPortal(
    <div className="upgrade-veil" onClick={onClose} data-testid="upgrade-modal">
      <div className="upgrade-card" onClick={(e) => e.stopPropagation()}
           role="dialog" aria-modal="true" aria-labelledby="upgrade-title">
        <button className="upgrade-x" onClick={onClose} aria-label="Close"
                data-testid="upgrade-close">×</button>

        <span className="upgrade-eyebrow">Creator · 100 clips/month</span>
        <h3 id="upgrade-title">{copy.title}</h3>
        <p className="upgrade-sub">{copy.body(limits)}</p>

        <div className="upgrade-actions">
          <button className="btn btn-ghost btn-sm" onClick={onClose}
                  data-testid="upgrade-later">
            Not now
          </button>
          <button className="btn btn-primary btn-shine" onClick={onSeePlans}
                  data-testid="upgrade-see-plans">
            View plans
          </button>
        </div>
        <p className="upgrade-foot">You will review the price before confirming.</p>
      </div>
    </div>,
    document.body,
  );
}
