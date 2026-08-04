import { useEffect, useState } from "react";
import { getShare } from "./api";
import { useTheme } from "./ThemeToggle";

/* The public page for one clip.
 *
 * Every clip already has a score breakdown that only its owner ever saw. This is
 * that breakdown, next to the clip, on a page a creator has an actual reason to
 * post -- which is why it sits behind the paid plan: it is distribution, and the
 * footer is the signup path.
 *
 * Rendered from the path (/s/<token>) rather than a route table, because the app
 * has no router and this is the only public path there is.
 */

const LABELS = {
  hook: "Hook", standalone: "Stands alone", emotion: "Emotion",
  ending: "Ending", payoff: "Payoff", share: "Shareable",
};

export function shareToken() {
  const m = window.location.pathname.match(/^\/s\/([\w-]{4,40})\/?$/);
  return m ? m[1] : null;
}

export default function SharePage({ token }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useTheme();

  useEffect(() => {
    getShare(token).then(setData).catch((e) => setError(e.message));
  }, [token]);

  if (error) {
    return (
      <div className="share-page">
        <div className="share-empty" data-testid="share-error">
          <h1>This link isn’t live.</h1>
          <p>{error}</p>
          <a className="btn btn-primary" href="/">Make your own clips</a>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="share-page">
        <div className="share-wrap">
          <div className="share-video skeleton" />
          <div className="share-meta">
            <span className="skel-line w60" />
            <span className="skel-line w45" />
            <span className="skel-line" />
          </div>
        </div>
      </div>
    );
  }

  const entries = Object.entries(data.scores || {});

  return (
    <div className="share-page" data-testid="share-page">
      <div className="share-wrap">
        <div className="share-video">
          <video src={data.video} controls playsInline preload="metadata"
                 data-testid="share-video" />
        </div>

        <div className="share-meta">
          {data.score != null && (
            <span className="share-score" data-testid="share-score">
              <strong>{Number(data.score).toFixed(1)}</strong>
              <em>virality score</em>
            </span>
          )}

          <h1 data-testid="share-title">{data.title || "Untitled clip"}</h1>
          {data.hook && <p className="share-hook" data-testid="share-hook">“{data.hook}”</p>}

          {entries.length > 0 && (
            <div className="share-bars" data-testid="share-bars">
              {entries.map(([k, v]) => (
                <div className="share-bar" key={k}>
                  <span>{LABELS[k] || k}</span>
                  <span className="share-bar-track">
                    <span className="share-bar-fill"
                          style={{ width: `${Math.min(100, (Number(v) / 10) * 100)}%` }} />
                  </span>
                  <em>{Number(v).toFixed(0)}</em>
                </div>
              ))}
            </div>
          )}

          <p className="share-stats">
            {data.duration ? `${Math.round(data.duration)}s` : null}
            {data.duration && data.views ? " · " : null}
            {data.views ? `${data.views} view${data.views === 1 ? "" : "s"}` : null}
          </p>
        </div>
      </div>

      <footer className="share-foot">
        <span>
          <strong>Made with Clipper.</strong> It reads a long video, finds the moments
          worth posting, and cuts them vertical with word-accurate captions.
        </span>
        <a className="btn btn-primary btn-shine" href="/" data-testid="share-cta">
          Clip your own video — free
        </a>
      </footer>
    </div>
  );
}
