import { useEffect, useState } from "react";
import { listJobs, getBilling, startCheckout, clipUrl } from "./api";

function when(iso) {
  if (!iso) return "";
  const then = new Date(iso);
  const mins = Math.round((Date.now() - then.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return then.toLocaleDateString();
}

function sourceLabel(url) {
  try {
    const u = new URL(url);
    return (u.searchParams.get("v") || u.pathname.replace("/", "")) || u.hostname;
  } catch {
    return url.slice(0, 40);
  }
}

/**
 * Signed-in home: credit balance, top-ups, and every past job.
 *
 * Persistence is what makes this possible -- before jobs were stored, closing
 * the tab lost the clips.
 */
export default function Dashboard({ user, onOpenJob, onClose, onCreditsChanged }) {
  const [jobs, setJobs] = useState(null);
  const [billing, setBilling] = useState(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(null);

  useEffect(() => {
    listJobs().then(setJobs).catch(() => setJobs([]));
    getBilling().then(setBilling).catch(() => {});
  }, []);

  async function buy(credits) {
    setBusy(credits);
    setNotice("");
    try {
      const r = await startCheckout({ credits });
      setNotice(
        r.status === "provider_not_configured"
          ? "Checkout isn't connected yet — payments are coming shortly."
          : "Redirecting…"
      );
    } catch (e) {
      setNotice(e.message);
    } finally {
      setBusy(null);
    }
  }

  const done = (jobs || []).filter((j) => j.status === "done");
  const totalClips = done.reduce((n, j) => n + (j.clips?.length || 0), 0);

  return (
    <div className="dash">
      <div className="container">
        <header className="dash-head">
          <div>
            <h2>Your clips</h2>
            <p className="dash-sub">{user.email}</p>
          </div>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>
            New clip
          </button>
        </header>

        <div className="dash-stats">
          <div className="dash-stat">
            <strong>{billing?.credits ?? user.credits}</strong>
            <span>credits left</span>
          </div>
          <div className="dash-stat">
            <strong>{done.length}</strong>
            <span>videos processed</span>
          </div>
          <div className="dash-stat">
            <strong>{totalClips}</strong>
            <span>clips made</span>
          </div>
        </div>

        {/* Top-ups live here rather than only on the pricing page: this is where
            someone is when they notice they are running low. */}
        <section className="dash-section">
          <h3>Add credits</h3>
          <p className="dash-hint">One-off top-ups. These never expire.</p>
          <div className="topup-row">
            {[
              { credits: 500, usd: 12 },
              { credits: 1500, usd: 30 },
              { credits: 5000, usd: 90 },
            ].map((t) => (
              <button
                key={t.credits}
                className="topup topup-buy"
                onClick={() => buy(t.credits)}
                disabled={busy === t.credits}
              >
                <strong>{t.credits.toLocaleString()}</strong>
                <span>credits</span>
                <em>{busy === t.credits ? "…" : `$${t.usd}`}</em>
              </button>
            ))}
          </div>
          {notice && <p className="dash-notice">{notice}</p>}
        </section>

        <section className="dash-section">
          <h3>History</h3>

          {jobs === null && <p className="dash-hint">Loading…</p>}

          {jobs !== null && jobs.length === 0 && (
            <div className="dash-empty">
              <p>No videos yet.</p>
              <button className="btn btn-primary btn-sm" onClick={onClose}>
                Clip your first video
              </button>
            </div>
          )}

          <div className="job-list">
            {(jobs || []).map((job) => (
              <button
                key={job.id}
                className="job-row"
                onClick={() => onOpenJob(job)}
                disabled={job.status !== "done"}
              >
                <span className={`job-status s-${job.status}`}>
                  {job.status === "done" ? "✓"
                    : job.status === "failed" ? "!"
                    : job.status === "expired" ? "–"
                    : "•"}
                </span>

                <span className="job-main">
                  <span className="job-title">
                    {job.clips?.[0]?.title || sourceLabel(job.url)}
                  </span>
                  <span className="job-meta">
                    {job.status === "done"
                      ? `${job.clips.length} clip${job.clips.length === 1 ? "" : "s"}`
                      : job.status === "expired"
                      ? "Clips expired — re-run the video to regenerate them"
                      : job.status === "failed"
                      ? job.error?.slice(0, 70) || "Failed"
                      : job.progress_message || job.status}
                    {" · "}
                    {when(job.created_at)}
                  </span>
                </span>

                {job.status === "done" && job.clips?.length > 0 && (
                  <span className="job-thumbs">
                    {job.clips.slice(0, 3).map((c, i) => (
                      <video
                        key={i}
                        src={clipUrl(job.id, c.file)}
                        muted
                        preload="metadata"
                        aria-hidden="true"
                      />
                    ))}
                  </span>
                )}
              </button>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
