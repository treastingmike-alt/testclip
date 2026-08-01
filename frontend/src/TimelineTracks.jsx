import { useCallback, useEffect, useRef, useState } from "react";
import { logoSrc } from "./EditorPresets";

/**
 * Layered timeline: one row per kind of thing in the clip.
 *
 * The trim bar above answers "which part of the source is this clip". This
 * answers "what is on screen, when" -- a different question, which is why it is
 * a separate strip rather than more handles on the same bar.
 *
 * Only the overlay track is editable. Captions, video and audio are shown as
 * read-only context: they span the whole clip by definition, and drawing them
 * makes the overlay bars legible as *layers* rather than floating rectangles.
 */

const MIN_SPAN = 0.3;      // a bar thinner than this cannot be grabbed

function fmt(t) {
  return `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
}

export default function TimelineTracks({
  clipDuration, now, overlays, selected, onSelect, onChange, onSeek,
}) {
  const railRef = useRef(null);
  const [drag, setDrag] = useState(null);   // {index, mode, grabDx}

  const pct = (t) => `${Math.min(Math.max(t / clipDuration, 0), 1) * 100}%`;

  const timeAt = useCallback((clientX) => {
    const rail = railRef.current;
    if (!rail) return 0;
    const r = rail.getBoundingClientRect();
    return Math.min(Math.max((clientX - r.left) / r.width, 0), 1) * clipDuration;
  }, [clipDuration]);

  useEffect(() => {
    if (!drag) return;
    function move(e) {
      const t = timeAt(e.clientX);
      const ov = overlays[drag.index];
      if (!ov) return;
      const s = ov.t_start ?? 0;
      const en = ov.t_end ?? clipDuration;

      if (drag.mode === "start") {
        onChange(drag.index, { t_start: Math.min(t, en - MIN_SPAN) });
      } else if (drag.mode === "end") {
        onChange(drag.index, { t_end: Math.max(t, s + MIN_SPAN) });
      } else {
        // Whole-bar move keeps its length and stays inside the clip.
        const span = en - s;
        const ns = Math.min(Math.max(t - drag.grabDx, 0), clipDuration - span);
        onChange(drag.index, { t_start: ns, t_end: ns + span });
      }
    }
    const up = () => setDrag(null);
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
  }, [drag, overlays, clipDuration, onChange, timeAt]);

  const rows = [
    { key: "socials", label: "Socials" },
    { key: "captions", label: "Captions" },
    { key: "video", label: "Video" },
    { key: "audio", label: "Audio" },
  ];

  return (
    <div className="tracks">
      <div className="tracks-head">
        <span className="tracks-title">Layers</span>
        <span className="tracks-time">{fmt(now)} / {fmt(clipDuration)}</span>
      </div>

      <div className="tracks-body">
        <div className="tracks-labels">
          {rows.map((r) => (
            <span key={r.key} className="track-label">{r.label}</span>
          ))}
        </div>

        <div className="tracks-rail" ref={railRef}
             onMouseDown={(e) => {
               // Clicking empty rail scrubs, which is what a timeline should do.
               if (e.target.closest(".ov-bar")) return;
               onSeek?.(timeAt(e.clientX));
             }}>
          {rows.map((r) => (
            <div key={r.key} className={`track-row ${r.key}`}>
              {r.key === "socials" ? (
                overlays.length === 0 ? (
                  <span className="track-empty">Add a handle to place it in time</span>
                ) : overlays.map((o, i) => {
                  const s = o.t_start ?? 0;
                  const e = o.t_end ?? clipDuration;
                  return (
                    <div
                      key={i}
                      className={`ov-bar ${selected === i ? "sel" : ""}`}
                      style={{ left: pct(s), width: `calc(${pct(e)} - ${pct(s)})` }}
                      onMouseDown={(ev) => {
                        ev.stopPropagation();
                        onSelect(i);
                        setDrag({ index: i, mode: "move", grabDx: timeAt(ev.clientX) - s });
                      }}
                      title={`${o.text || "overlay"} · ${s.toFixed(1)}s – ${e.toFixed(1)}s`}
                    >
                      <span className="ov-bar-grip start"
                            onMouseDown={(ev) => { ev.stopPropagation(); onSelect(i); setDrag({ index: i, mode: "start" }); }} />
                      {o.platform && <img src={logoSrc(o.platform)} alt="" className="ov-bar-logo" />}
                      <span className="ov-bar-text">{o.text || "text"}</span>
                      <span className="ov-bar-grip end"
                            onMouseDown={(ev) => { ev.stopPropagation(); onSelect(i); setDrag({ index: i, mode: "end" }); }} />
                    </div>
                  );
                })
              ) : (
                /* Read-only context: these span the clip by definition. */
                <div className={`track-fill ${r.key}`}>
                  <span>{r.key === "captions" ? "Word-by-word captions"
                        : r.key === "video" ? "Clip" : "Original audio"}</span>
                </div>
              )}
            </div>
          ))}

          <span className="tracks-playhead" style={{ left: pct(now) }} />
        </div>
      </div>
    </div>
  );
}
