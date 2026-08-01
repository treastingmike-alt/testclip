import { useEffect, useMemo, useRef, useState } from "react";
import { getTranscript, rerenderClip, clipUrl } from "./api";

const FONTS = [
  { id: null, name: "Style default", css: "inherit" },
  { id: "anton", name: "Anton", css: "'Anton', sans-serif" },
  { id: "archivo", name: "Archivo", css: "'Archivo Black', sans-serif" },
  { id: "bangers", name: "Bangers", css: "'Bangers', cursive" },
  { id: "poppins", name: "Poppins", css: "'Poppins', sans-serif" },
  { id: "mukta", name: "Mukta", css: "'Mukta', sans-serif" },
];

const LANGUAGES = [
  { code: null, name: "Original language" },
  { code: "en", name: "English" },
  { code: "hi", name: "Hindi" },
  { code: "es", name: "Spanish" },
  { code: "pt", name: "Portuguese" },
  { code: "fr", name: "French" },
  { code: "de", name: "German" },
  { code: "ar", name: "Arabic" },
  { code: "id", name: "Indonesian" },
  { code: "ja", name: "Japanese" },
];

const CAPTION_STYLES = [
  { id: "classic", name: "Classic", chip: "#d8f24e" },
  { id: "fitbox", name: "Fit box", chip: "#2b6cf6" },
  { id: "tech", name: "Tech", chip: "#7fd4f0" },
  { id: "business", name: "Business", chip: "#efeae2" },
  { id: "gameplay", name: "Gameplay", chip: "#3ad43a" },
];

/* How much source context to show around the clip. Showing the whole video
   would make a 40-minute source unusable at this width; a window either side is
   enough to find the real in/out point. */
const CONTEXT_SECONDS = 30;
const MIN_CLIP_SECONDS = 3;

function fmt(t) {
  const s = Math.max(0, t);
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}.${String(Math.floor((s % 1) * 10))}`;
}

/**
 * Trim/extend a rendered clip against the stored transcript.
 *
 * The transcript is the real editing surface here -- dragging pixels is
 * imprecise, but clicking the line where a thought starts is exact, so the
 * timeline and the transcript are wired to each other in both directions.
 */
export default function ClipEditor({ job, clip, index, onClose, onSaved }) {
  const [transcript, setTranscript] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [start, setStart] = useState(clip.start);
  const [end, setEnd] = useState(clip.end);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [dragging, setDragging] = useState(null); // "start" | "end" | null
  const [capStyle, setCapStyle] = useState(null);       // null = keep template style
  const [capFont, setCapFont] = useState(null);         // null = keep style's font
  const [translateTo, setTranslateTo] = useState(null); // null = original language
  const [lineEdits, setLineEdits] = useState({});       // utterance idx -> edited text
  const trackRef = useRef(null);
  const videoRef = useRef(null);

  useEffect(() => {
    let alive = true;
    getTranscript(job.id)
      .then((t) => alive && setTranscript(t))
      .catch((e) => alive && setLoadError(e.message));
    return () => { alive = false; };
  }, [job.id]);

  // Visible window: the clip plus context, clamped to the real source length.
  const view = useMemo(() => {
    const total = transcript?.duration ?? clip.end + CONTEXT_SECONDS;
    return {
      from: Math.max(0, clip.start - CONTEXT_SECONDS),
      to: Math.min(total, clip.end + CONTEXT_SECONDS),
      total,
    };
  }, [transcript, clip.start, clip.end]);

  const span = Math.max(view.to - view.from, 0.001);
  const pct = (t) => ((t - view.from) / span) * 100;

  function timeAt(clientX) {
    const rect = trackRef.current.getBoundingClientRect();
    const ratio = Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1);
    return view.from + ratio * span;
  }

  useEffect(() => {
    if (!dragging) return;

    function onMove(e) {
      const t = timeAt(e.clientX ?? e.touches?.[0]?.clientX);
      if (dragging === "start") setStart(Math.min(t, end - MIN_CLIP_SECONDS));
      else setEnd(Math.max(t, start + MIN_CLIP_SECONDS));
    }
    function onUp() { setDragging(null); }

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    window.addEventListener("touchmove", onMove);
    window.addEventListener("touchend", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("touchmove", onMove);
      window.removeEventListener("touchend", onUp);
    };
  }, [dragging, start, end, view.from, span]);

  const duration = end - start;
  const captionsChanged = capStyle !== null || capFont !== null ||
    translateTo !== null || Object.keys(lineEdits).length > 0;
  const changed = captionsChanged ||
    Math.abs(start - clip.start) > 0.05 || Math.abs(end - clip.end) > 0.05;

  async function save() {
    setSaving(true);
    setSaveError("");
    try {
      // Only utterances inside the clip, with any text edits applied.
      const lines = (transcript?.utterances || [])
        .map((u, i) => ({ ...u, i }))
        .filter((u) => u.end > start && u.start < end)
        .map((u) => ({
          start: Math.max(u.start, start),
          end: Math.min(u.end, end),
          text: lineEdits[u.i] ?? u.text,
        }));
      const updated = await rerenderClip(job.id, index, start, end, {
        captionStyle: capStyle,
        captionFont: capFont,
        translateTo,
        captionLines: Object.keys(lineEdits).length ? lines : null,
      });
      onSaved(index, updated);
      onClose();
    } catch (e) {
      setSaveError(e.message);
    } finally {
      setSaving(false);
    }
  }

  /* Preview seeks relative to the CLIP, but transcript times are absolute in the
     SOURCE -- so clicking a line seeks to (absolute - clip.start). Lines outside
     the currently rendered clip cannot be previewed until the edit is saved. */
  function seekTo(absolute) {
    const v = videoRef.current;
    if (!v) return;
    const rel = absolute - clip.start;
    if (rel >= 0 && rel <= clip.end - clip.start) {
      v.currentTime = rel;
      v.play().catch(() => {});
    }
  }

  return (
    <div className="editor-backdrop" onClick={onClose}>
      <div className="editor" onClick={(e) => e.stopPropagation()}>
        <header className="editor-head">
          <div>
            <h3>Edit clip length</h3>
            <p className="editor-sub">{clip.title}</p>
          </div>
          <button className="editor-close" onClick={onClose} aria-label="Close">×</button>
        </header>

        <div className="editor-body">
          <div className="editor-preview">
            <video
              ref={videoRef}
              src={clipUrl(job.id, clip.file)}
              controls
              playsInline
              preload="metadata"
            />
            <div className="editor-readout">
              <span><strong>{fmt(start)}</strong> → <strong>{fmt(end)}</strong></span>
              <span className={duration < MIN_CLIP_SECONDS ? "bad" : ""}>
                {duration.toFixed(1)}s
              </span>
            </div>
          </div>

          <div className="editor-transcript">
            {loadError && <div className="editor-error">{loadError}</div>}
            {!transcript && !loadError && <div className="editor-loading">Loading transcript…</div>}
            {transcript?.utterances?.map((u, i) => {
              const inClip = u.end > start && u.start < end;
              if (!inClip) {
                return (
                  <button
                    type="button"
                    key={i}
                    className="tx-line"
                    onClick={() => seekTo(u.start)}
                    title="Jump to this line"
                  >
                    <span className="tx-time">{fmt(u.start)}</span>
                    <span className="tx-text">{u.text}</span>
                  </button>
                );
              }
              /* Inside the clip the line is a caption -- so it is editable.
                 Fixing a mis-heard word should not need another tool. */
              const edited = lineEdits[i] !== undefined;
              return (
                <div key={i} className={`tx-line in editable ${edited ? "edited" : ""}`}>
                  <button
                    type="button"
                    className="tx-time as-btn"
                    onClick={() => seekTo(u.start)}
                    title="Jump to this line"
                  >
                    {fmt(u.start)}
                  </button>
                  <input
                    className="tx-input"
                    value={lineEdits[i] ?? u.text}
                    onChange={(e) => setLineEdits({ ...lineEdits, [i]: e.target.value })}
                    aria-label={`Caption at ${fmt(u.start)}`}
                  />
                  {edited && (
                    <button
                      type="button"
                      className="tx-undo"
                      title="Undo edit"
                      onClick={() => {
                        const next = { ...lineEdits };
                        delete next[i];
                        setLineEdits(next);
                      }}
                    >
                      ↺
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        <div className="editor-timeline">
          <div className="tl-track" ref={trackRef}>
            {/* utterance ticks give the bar a sense of where speech actually is */}
            {transcript?.utterances?.map((u, i) => (
              <span
                key={i}
                className="tl-tick"
                style={{ left: `${pct(u.start)}%`, width: `${Math.max(pct(u.end) - pct(u.start), 0.3)}%` }}
              />
            ))}
            <span
              className="tl-selection"
              style={{ left: `${pct(start)}%`, width: `${pct(end) - pct(start)}%` }}
            />
            <span
              className="tl-handle"
              style={{ left: `${pct(start)}%` }}
              onMouseDown={() => setDragging("start")}
              onTouchStart={() => setDragging("start")}
              role="slider"
              aria-label="Clip start"
              aria-valuenow={start}
            />
            <span
              className="tl-handle end"
              style={{ left: `${pct(end)}%` }}
              onMouseDown={() => setDragging("end")}
              onTouchStart={() => setDragging("end")}
              role="slider"
              aria-label="Clip end"
              aria-valuenow={end}
            />
          </div>
          <div className="tl-scale">
            <span>{fmt(view.from)}</span>
            <span>{fmt(view.to)}</span>
          </div>
        </div>

        <div className="editor-styles">
          <span className="styles-label">Captions</span>
          <div className="style-chips">
            <button
              type="button"
              className={`style-chip ${capStyle === null ? "on" : ""}`}
              onClick={() => setCapStyle(null)}
            >
              Template default
            </button>
            {CAPTION_STYLES.map((cs) => (
              <button
                type="button"
                key={cs.id}
                className={`style-chip ${capStyle === cs.id ? "on" : ""}`}
                onClick={() => setCapStyle(cs.id)}
              >
                <i style={{ background: cs.chip }} />
                {cs.name}
              </button>
            ))}
          </div>
        </div>

        <div className="editor-styles">
          <span className="styles-label">Font</span>
          <div className="style-chips">
            {FONTS.map((f) => (
              <button
                type="button"
                key={f.id || "default"}
                className={`style-chip ${capFont === f.id ? "on" : ""}`}
                style={{ fontFamily: f.css }}
                onClick={() => setCapFont(f.id)}
              >
                {f.name}
              </button>
            ))}
          </div>
        </div>

        <div className="editor-styles">
          <span className="styles-label">
            Subtitles <em className="pro-badge">Pro</em>
          </span>
          <select
            className="lang-select"
            value={translateTo || ""}
            onChange={(e) => setTranslateTo(e.target.value || null)}
            aria-label="Subtitle language"
          >
            {LANGUAGES.map((l) => (
              <option key={l.code || "orig"} value={l.code || ""}>{l.name}</option>
            ))}
          </select>
          {translateTo && (
            <span className="lang-note">
              Audio stays original — captions become whole lines, timed to the speech.
            </span>
          )}
        </div>

        <footer className="editor-foot">
          <div className="editor-nudge">
            <button type="button" onClick={() => setStart((s) => Math.max(view.from, s - 0.5))}>
              ← start
            </button>
            <button type="button" onClick={() => setStart((s) => Math.min(end - MIN_CLIP_SECONDS, s + 0.5))}>
              start →
            </button>
            <button type="button" onClick={() => setEnd((e) => Math.max(start + MIN_CLIP_SECONDS, e - 0.5))}>
              ← end
            </button>
            <button type="button" onClick={() => setEnd((e) => Math.min(view.to, e + 0.5))}>
              end →
            </button>
          </div>

          {saveError && <div className="editor-error">{saveError}</div>}

          <div className="editor-actions">
            <button className="btn btn-ghost btn-sm" onClick={onClose} disabled={saving}>
              Cancel
            </button>
            <button
              className="btn btn-primary btn-sm"
              onClick={save}
              disabled={!changed || saving || duration < MIN_CLIP_SECONDS}
            >
              {saving ? "Re-rendering…" : "Save changes"}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
