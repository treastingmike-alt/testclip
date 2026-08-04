import { useEffect, useState } from "react";

/* The studio, as three deliberate steps.
 *
 * It used to be one screen where the source row, a collapsible "Output
 * settings" drawer and the Generate button all coexisted -- so the order of
 * operations was implied by vertical position, the settings were hidden behind a
 * chevron by default, and nothing ever confirmed what was about to run. Worse,
 * a free account met its ceilings as a rejected request AFTER pressing Generate.
 *
 * Source -> Preferences -> Review. Each step answers one question, the limits
 * are stated before they bite, and the last step shows the whole recipe and its
 * price before anything is spent.
 */

const STEPS = [
  { n: 1, id: "source", name: "Source", hint: "The video you want clipped" },
  { n: 2, id: "prefs", name: "Preferences", hint: "How the clips should come out" },
  { n: 3, id: "review", name: "Review", hint: "Check it, then generate" },
];

const LENGTHS = {
  any: "Any length", short: "Under 30s", medium: "30–60s",
  long: "60–90s", extended: "Over 90s",
};

function LockIcon() {
  return (
    <svg className="chip-lock" viewBox="0 0 24 24" width="13" height="13"
         fill="none" aria-hidden="true">
      <rect x="5" y="11" width="14" height="9" rx="2.2" stroke="currentColor" strokeWidth="2" />
      <path d="M8.5 11V8a3.5 3.5 0 0 1 7 0v3" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" />
    </svg>
  );
}

/* One switch, one locked state, one place. Every gated boolean in step 2 renders
   through this so a locked control always behaves the same way: it is visible,
   it says why, and pressing it explains rather than doing nothing. */
function ToggleChip({ label, on, onChange, locked, lockReason, title, testid }) {
  return (
    <button
      type="button"
      className={`chip toggle ${on && !locked ? "active" : ""} ${locked ? "locked" : ""}`}
      onClick={() => (locked ? lockReason() : onChange(!on))}
      role={locked ? undefined : "switch"}
      aria-checked={locked ? undefined : on}
      title={locked ? "Creator plan — click to see what it includes" : title}
      data-testid={testid}
    >
      {locked ? <LockIcon /> : <span className="chip-switch" aria-hidden="true" />}
      {label}
    </button>
  );
}

function StepRail({ step, maxReached, onGo }) {
  return (
    <ol className="wiz-rail" data-testid="wizard-rail">
      <span className="wiz-rail-fill"
            style={{ width: `${((step - 1) / (STEPS.length - 1)) * 100}%` }}
            aria-hidden="true" />
      {STEPS.map((s) => {
        const state = s.n < step ? "done" : s.n === step ? "now" : "todo";
        return (
          <li key={s.id} className={`wiz-step ${state}`}>
            <button type="button"
                    onClick={() => s.n <= maxReached && onGo(s.n)}
                    disabled={s.n > maxReached}
                    data-testid={`wizard-step-${s.id}`}>
              <span className="wiz-num" aria-hidden="true">
                {state === "done" ? (
                  <svg viewBox="0 0 24 24" width="13" height="13" fill="none">
                    <path d="m5 13 4 4L19 7" stroke="currentColor" strokeWidth="3"
                          strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                ) : s.n}
              </span>
              <span className="wiz-label">
                <strong>{s.name}</strong>
                <em>{s.hint}</em>
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function Row({ label, value, muted }) {
  return (
    <div className={`review-row ${muted ? "muted" : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export default function StudioWizard({
  step, setStep, source, prefs, plan, templates, previewManifest,
  TemplatePreview, SourceTabs, DropZone, SourcePreview,
  submitting, submitError, onGenerate, onUpgrade,
}) {
  const [maxReached, setMaxReached] = useState(step);
  useEffect(() => { setMaxReached((m) => Math.max(m, step)); }, [step]);

  const { limits, can, isFree } = plan;
  const sourceReady = source.mode === "upload" ? !!source.file : !!source.url.trim();
  const lockedTemplates = templates.filter((t) => t.id === "gameplay" && !can("gameplay"));
  // What is actually locked, rather than what the plan is called -- see usePlan.
  const anyLocked = lockedTemplates.length > 0
    || !can("tighten_pauses") || !can("multilingual");

  const clipOptions = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
  const cost = prefs.nClips * plan.creditsPerClip;

  function next() {
    if (step === 1 && !sourceReady) return;
    setStep(step + 1);
  }

  return (
    <div className="wizard" data-testid="studio-wizard">
      <StepRail step={step} maxReached={maxReached} onGo={setStep} />

      {/* ---------------- 1. Source ---------------- */}
      {step === 1 && (
        <section className="wiz-panel" data-testid="wizard-panel-source">
          <header className="wiz-head">
            <h3>Where is the video?</h3>
            <p>Paste a public link, or upload a file from this device.</p>
          </header>

          <SourceTabs value={source.mode} onChange={source.setMode}
                      className="workspace-source-tabs" />

          {source.mode === "link" ? (
            <div className="workspace-urlrow">
              <input type="url" placeholder="Paste a public video link..."
                     value={source.url}
                     onChange={(e) => source.setUrl(e.target.value)}
                     onKeyDown={(e) => e.key === "Enter" && sourceReady && next()}
                     aria-label="Public video URL"
                     data-testid="studio-url-input" />
            </div>
          ) : (
            <DropZone file={source.file} onFile={source.setFile} />
          )}

          {source.mode === "link" && (
            <SourcePreview preview={source.preview} loading={source.loading}
                           error={source.error} uploadUrl="" uploadFile={null} />
          )}

          {/* Stated before it bites, not as a rejection after Generate. */}
          <p className="wiz-limits" data-testid="wizard-limits">
            Your plan: up to <strong>{limits.max_clips} clips</strong> per video,
            sources up to <strong>{limits.max_source_minutes} min</strong>
            {source.mode === "upload" && <>, uploads up to <strong>{limits.max_upload_mb} MB</strong></>}.
            {isFree && (
              <button type="button" className="wiz-limits-link" onClick={() => onUpgrade("clips")}
                      data-testid="limits-upgrade-link">
                Need more?
              </button>
            )}
          </p>
        </section>
      )}

      {/* ---------------- 2. Preferences ---------------- */}
      {step === 2 && (
        <section className="wiz-panel" data-testid="wizard-panel-prefs">
          <header className="wiz-head">
            <h3>How should the clips come out?</h3>
            <p>Every one of these can still be changed per clip in the editor afterwards.</p>
          </header>

          <div className="pref-group">
            <span className="pref-group-label">Output</span>
            <div className="options-row">
              <label className="chip">
                Clips
                <select value={prefs.nClips} data-testid="opt-nclips"
                        onChange={(e) => {
                          const n = Number(e.target.value);
                          if (n > limits.max_clips) { onUpgrade("clips"); return; }
                          prefs.setNClips(n);
                        }}>
                  {clipOptions.map((n) => (
                    <option key={n} value={n} disabled={n > limits.max_clips}>
                      {n}{n > limits.max_clips ? " — Creator" : ""}
                    </option>
                  ))}
                </select>
              </label>
              <label className="chip">
                Ratio
                <select value={prefs.ratio} onChange={(e) => prefs.setRatio(e.target.value)}
                        data-testid="opt-ratio">
                  <option value="9:16">9:16</option>
                  <option value="1:1">1:1</option>
                  <option value="16:9">16:9</option>
                </select>
              </label>
              <label className="chip">
                Length
                <select value={prefs.lengthPref}
                        onChange={(e) => prefs.setLengthPref(e.target.value)}
                        data-testid="opt-length">
                  {Object.entries(LENGTHS).map(([id, name]) => (
                    <option key={id} value={id}>{name}</option>
                  ))}
                </select>
              </label>
            </div>
          </div>

          <div className="pref-group">
            <span className="pref-group-label">Captions and audio</span>
            <div className="options-row">
              <ToggleChip label="Captions" on={prefs.burnSubtitles}
                          onChange={prefs.setBurnSubtitles} testid="opt-captions"
                          title="Word-timed captions burned into the video" />
              <ToggleChip label="Auto-censor" on={prefs.autoCensor}
                          onChange={prefs.setAutoCensor} testid="opt-censor"
                          title="Mutes profanity and stars it in captions, so clips stay monetisable" />
              <ToggleChip label="Mixed language" on={prefs.multilingual}
                          onChange={prefs.setMultilingual}
                          locked={!can("multilingual")}
                          lockReason={() => onUpgrade("multilingual")}
                          testid="opt-multilingual"
                          title="Transcribes speech that switches language mid-sentence" />
            </div>
          </div>

          <div className="pref-group">
            <span className="pref-group-label">Pacing</span>
            <div className="options-row">
              <ToggleChip label="Tighten pauses" on={prefs.tightenPauses}
                          onChange={prefs.setTightenPauses}
                          locked={!can("tighten_pauses")}
                          lockReason={() => onUpgrade("tighten_pauses")}
                          testid="opt-tighten"
                          title="Cuts the dead air so a clip feels edited rather than trimmed" />
            </div>
          </div>

          <div className="pref-group">
            <span className="pref-group-label">Template</span>
            <div className="template-grid">
              {templates.map((t) => {
                const locked = lockedTemplates.includes(t);
                const selected = prefs.template === t.id && !locked;
                return (
                  <button
                    type="button"
                    key={t.id}
                    className={`template-card ${selected ? "selected" : ""} ${locked ? "locked" : ""}`}
                    onClick={() => (locked ? onUpgrade("gameplay") : prefs.setTemplate(t.id))}
                    aria-pressed={selected}
                    data-testid={`opt-template-${t.id}`}
                  >
                    <TemplatePreview template={t} variants={previewManifest[t.id]} />
                    <span className="template-name">
                      {t.name}
                      {locked && <LockIcon />}
                    </span>
                    <span className="template-note">
                      {locked ? "Creator plan" : t.note}
                    </span>
                    {selected && <span className="template-tick">✓</span>}
                  </button>
                );
              })}
            </div>
          </div>

          <label className="intent-field">
            <span className="intent-label">
              Find clip moment <span className="intent-optional">Optional</span>
            </span>
            <input type="text" value={prefs.intent} data-testid="opt-intent"
                   onChange={(e) => prefs.setIntent(e.target.value)}
                   placeholder="For example: when he talks about pricing." />
          </label>

          {/* One strip, at the end of the step. Not a banner per locked control:
              that reads as nagging, and nagging is what makes people leave. */}
          {anyLocked && (
            <div className="unlock-strip" data-testid="unlock-strip">
              <span>
                <strong>Locked above: gameplay split-screen, pause tightening, mixed language.</strong>
                <em>Creator also removes the watermark and turns on share pages.</em>
              </span>
              <button className="btn btn-primary btn-sm btn-shine"
                      onClick={() => onUpgrade()} data-testid="unlock-strip-btn">
                What's in Creator?
              </button>
            </div>
          )}
        </section>
      )}

      {/* ---------------- 3. Review ---------------- */}
      {step === 3 && (
        <section className="wiz-panel" data-testid="wizard-panel-review">
          <header className="wiz-head">
            <h3>Ready to generate.</h3>
            <p>This is what will run. Nothing is spent until you press the button.</p>
          </header>

          <div className="review-card">
            <Row label="Source" value={source.mode === "upload"
              ? (source.file?.name || "Uploaded file")
              : (source.preview?.title || source.url)} />
            <Row label="Clips" value={`${prefs.nClips} × ${LENGTHS[prefs.lengthPref].toLowerCase()}`} />
            <Row label="Format" value={`${prefs.ratio} · ${templates.find((t) => t.id === prefs.template)?.name || prefs.template}`} />
            <Row label="Captions" value={prefs.burnSubtitles ? "Burned in, word-timed" : "Off"} />
            <Row label="Auto-censor" value={prefs.autoCensor ? "On" : "Off"} />
            <Row label="Tighten pauses"
                 value={prefs.tightenPauses && can("tighten_pauses") ? "On" : "Off"}
                 muted={!can("tighten_pauses")} />
            <Row label="Mixed language"
                 value={prefs.multilingual && can("multilingual") ? "On" : "Off"}
                 muted={!can("multilingual")} />
            {prefs.intent.trim() && <Row label="Looking for" value={prefs.intent.trim()} />}
            <Row label="Cost" value={plan.credits == null
              ? `${cost} credits`
              : `${cost} of your ${plan.credits} credits`} />
          </div>

          {plan.watermarked && (
            <div className="review-note" data-testid="watermark-note">
              <span>
                Free clips carry a small <strong>Made with Clipper</strong> credit at the
                foot of the frame.
              </span>
              <button type="button" className="wiz-limits-link"
                      onClick={() => onUpgrade("no_watermark")}
                      data-testid="watermark-upgrade-link">
                Remove it
              </button>
            </div>
          )}

          {submitError && <div className="error-box">{submitError}</div>}
        </section>
      )}

      <footer className="wiz-nav">
        <button className="btn btn-ghost btn-sm" data-testid="wizard-back"
                onClick={() => setStep(Math.max(1, step - 1))}
                disabled={step === 1 || submitting}>
          Back
        </button>
        <span className="wiz-nav-count">Step {step} of {STEPS.length}</span>
        {step < 3 ? (
          <button className="btn btn-primary btn-shine" onClick={next}
                  disabled={step === 1 && !sourceReady}
                  title={step === 1 && !sourceReady
                    ? (source.mode === "upload" ? "Choose a video file first"
                                                : "Paste a video link first")
                    : undefined}
                  data-testid="wizard-next">
            {step === 1 ? "Choose preferences" : "Review"}
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" aria-hidden="true">
              <path d="M5 12h14m0 0-6-6m6 6-6 6" stroke="currentColor" strokeWidth="2.2"
                    strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        ) : (
          <button className="btn btn-primary btn-shine" onClick={onGenerate}
                  disabled={submitting} data-testid="wizard-generate">
            {submitting ? "Starting..." : `Generate ${prefs.nClips} clip${prefs.nClips === 1 ? "" : "s"}`}
          </button>
        )}
      </footer>
    </div>
  );
}
