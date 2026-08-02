import { Fragment, useEffect, useRef, useState } from "react";
import { submitJob, getJob, clipUrl } from "./api";
import LiveEditor from "./LiveEditor";
import Dashboard from "./Dashboard";
import Pricing from "./Pricing";
import AuthModal from "./AuthModal";
import { fetchMe, logout } from "./api";

const STAGES = [
  { key: "downloading", label: "Fetching audio", blurb: "Pulling just the audio track — it's a fraction of the video." },
  { key: "transcribing", label: "Transcribing speech", blurb: "Every word, with millisecond-accurate timestamps." },
  { key: "analyzing", label: "Finding the best moments", blurb: "Reading the full transcript for hooks and payoffs." },
  { key: "rendering", label: "Rendering vertical clips", blurb: "Reframing to 9:16 and burning in captions." },
];

const STAGE_ORDER = ["queued", "downloading", "transcribing", "analyzing", "rendering", "done"];

function stageState(stageKey, jobStatus) {
  const current = STAGE_ORDER.indexOf(jobStatus);
  const mine = STAGE_ORDER.indexOf(stageKey);
  if (jobStatus === "done" || mine < current) return "done";
  if (mine === current) return "active";
  return "pending";
}

/* ---------- animation helpers ---------- */

/* Adds .in when the element scrolls into view */
function useReveal() {
  useEffect(() => {
    const els = document.querySelectorAll(".reveal");
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            e.target.classList.add("in");
            io.unobserve(e.target);
          }
        });
      },
      { threshold: 0.12 }
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);
}

/* Button that leans toward the cursor */
function Magnetic({ children, className = "", strength = 0.35, ...rest }) {
  const ref = useRef(null);

  function onMove(e) {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const x = (e.clientX - r.left - r.width / 2) * strength;
    const y = (e.clientY - r.top - r.height / 2) * strength;
    el.style.transform = `translate(${x}px, ${y}px)`;
  }

  function onLeave() {
    const el = ref.current;
    if (el) el.style.transform = "translate(0, 0)";
  }

  return (
    <button ref={ref} className={`magnetic ${className}`} onMouseMove={onMove} onMouseLeave={onLeave} {...rest}>
      {children}
    </button>
  );
}

/* Number that counts up when it enters the viewport */
function CountUp({ to, suffix = "", duration = 1200 }) {
  const ref = useRef(null);
  const [val, setVal] = useState(0);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        io.disconnect();
        const t0 = performance.now();
        const tick = (t) => {
          const p = Math.min((t - t0) / duration, 1);
          const eased = 1 - Math.pow(1 - p, 3);
          setVal(Math.round(to * eased));
          if (p < 1) requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      },
      { threshold: 0.6 }
    );
    io.observe(el);
    return () => io.disconnect();
  }, [to, duration]);

  return (
    <span ref={ref}>
      {val}
      {suffix}
    </span>
  );
}

/* Card with a light spotlight that follows the cursor */
function SpotlightCard({ children, className = "", style }) {
  const ref = useRef(null);

  function onMove(e) {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--sx", `${e.clientX - r.left}px`);
    el.style.setProperty("--sy", `${e.clientY - r.top}px`);
  }

  return (
    <div ref={ref} className={`spotlight ${className}`} style={style} onMouseMove={onMove}>
      {children}
    </div>
  );
}

/* ---------- decorative clip collage ---------- */

const FAN_CARDS = [
  { grad: "linear-gradient(160deg, #ffb46b, #ff5c39)", caption: "wait for it…", time: "0:24" },
  { grad: "linear-gradient(160deg, #7ce7d8, #1fa48f)", caption: "the best advice", time: "0:41" },
  { grad: "linear-gradient(160deg, #8a7bff, #4f46e5)", caption: "nobody tells you", time: "0:33" },
  { grad: "linear-gradient(160deg, #f9a8d4, #d9467c)", caption: "this changed everything", time: "0:19" },
  { grad: "linear-gradient(160deg, #c8f169, #5aa832)", caption: "here's the trick", time: "0:28" },
];

function ClipFan() {
  const ref = useRef(null);

  function onMove(e) {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--mx", (e.clientX - r.left) / r.width - 0.5);
    el.style.setProperty("--my", (e.clientY - r.top) / r.height - 0.5);
  }

  function onLeave() {
    const el = ref.current;
    if (!el) return;
    el.style.setProperty("--mx", 0);
    el.style.setProperty("--my", 0);
  }

  return (
    <div className="clipfan" ref={ref} onMouseMove={onMove} onMouseLeave={onLeave} aria-hidden="true">
      {FAN_CARDS.map((c, i) => (
        <div className={`fan-card f${i}`} key={i} style={{ "--grad": c.grad, "--depth": (i % 3) + 1 }}>
          <span className="fan-play">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none">
              <path d="M8 5.5v13l11-6.5-11-6.5Z" fill="currentColor" />
            </svg>
          </span>
          <span className="fan-caption">{c.caption}</span>
          <span className="fan-time">{c.time}</span>
        </div>
      ))}
    </div>
  );
}

/** Filename for a downloaded clip, built from its title.
 *
 * Keeps non-Latin scripts intact -- a Hindi clip should land in Downloads with
 * its Hindi title, not a transliteration. Only characters that actually break
 * filesystems are stripped. */
function downloadName(title, index) {
  const cleaned = (title || "")
    .replace(/[/\\?%*:|"<>]/g, "")   // illegal on macOS/Windows
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 70);
  return `${cleaned || `clip_${index + 1}`}.mp4`;
}

function fmtDuration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/* Ticking mm:ss since the job started */
function Elapsed({ since }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  return <>{fmtDuration((now - since) / 1000)}</>;
}

/* Circular percent ring */
function ProgressRing({ percent }) {
  const R = 54;
  const C = 2 * Math.PI * R;
  return (
    <div className="ring-wrap">
      <svg className="ring" viewBox="0 0 128 128">
        <circle className="ring-bg" cx="64" cy="64" r={R} />
        <circle
          className="ring-fg"
          cx="64"
          cy="64"
          r={R}
          strokeDasharray={C}
          strokeDashoffset={C - (C * Math.min(Math.max(percent, 0), 100)) / 100}
        />
      </svg>
      <span className="ring-label">
        <strong>{Math.round(percent)}</strong>
        <small>%</small>
      </span>
    </div>
  );
}

/* Full-width working panel shown while a job runs */
function ProcessingStudio({ job, startedAt, onCancel }) {
  const currentIdx = Math.max(
    STAGES.findIndex((s) => stageState(s.key, job.status) === "active"),
    0
  );
  const failed = job.status === "failed";
  const stageBase = (STAGE_ORDER.indexOf(job.status) - 1) / STAGES.length;
  const overall = failed
    ? 0
    : Math.min(99, Math.max(0, stageBase * 100 + (job.percent || 0) / STAGES.length));

  return (
    <div className={`studio ${failed ? "failed" : ""}`} aria-live="polite">
      <div className="studio-top">
        <ProgressRing percent={failed ? 0 : overall} />

        <div className="studio-head">
          <span className="studio-eyebrow">
            {failed ? "Something went wrong" : `Step ${currentIdx + 1} of ${STAGES.length}`}
          </span>
          <h3>{failed ? "We couldn't finish this one" : STAGES[currentIdx].label}</h3>
          <p className="studio-blurb">
            {failed ? "The details are below — most of the time it's an unsupported or speechless video." : STAGES[currentIdx].blurb}
          </p>
          <div className="studio-meta">
            <span className="studio-live">
              <span className="live-dot" aria-hidden="true" />
              {job.progress_message || "Working..."}
            </span>
            <span className="studio-timer">
              <Elapsed since={startedAt} /> elapsed
            </span>
          </div>
        </div>
      </div>

      <ol className="stage-rail">
        {STAGES.map((s, i) => {
          const state = failed && i === currentIdx ? "error" : stageState(s.key, job.status);
          return (
            <li key={s.key} className={`rail-item ${state}`}>
              <span className="rail-dot">
                {state === "done" ? (
                  <svg viewBox="0 0 24 24" width="12" height="12" fill="none">
                    <path d="m5 13 4 4L19 7" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                ) : state === "error" ? "!" : i + 1}
              </span>
              <span className="rail-label">{s.label}</span>
            </li>
          );
        })}
      </ol>

      {!failed && (
        <div className="skeleton-row" aria-hidden="true">
          {/* capped: at 10 clips a full row of skeletons becomes slivers */}
          {Array.from({ length: Math.min(job.options?.n_clips || 3, 5) }).map((_, i) => (
            <div className="skel-card" key={i} style={{ animationDelay: `${i * 160}ms` }}>
              <div className="skel-shimmer" />
              <span className="skel-line w70" />
              <span className="skel-line w45" />
            </div>
          ))}
        </div>
      )}

      {failed && (
        <>
          <div className="error-box">{job.error}</div>
          <button className="btn btn-primary" onClick={onCancel} style={{ marginTop: 18 }}>
            Try another video
          </button>
        </>
      )}
    </div>
  );
}

// Each card pairs a layout with a caption style -- users pick a look, not a
// matrix of independent switches. `words` drive a live word-by-word highlight
// in the preview, using the same colour and casing the renderer burns in, so
// the card shows what the output actually looks like rather than a swatch.
// Five distinct looks, each differing on framing AND caption treatment -- not
// just font size. `id` matches the backend TEMPLATES registry, so the card and
// the render can never disagree. `preview` is a real rendered clip if one has
// been generated (see backend/make_previews.py); otherwise the CSS mockup below
// stands in.
const TEMPLATES = [
  {
    id: "classic", name: "Classic", frame: "blur", scene: "scene-neutral",
    words: ["Here", "is", "your"], color: "#d8f24e", capClass: "cap-classic",
    note: "Blurred fill · coloured word",
  },
  {
    id: "fitvideo", name: "Fit video", frame: "fit", scene: "scene-black",
    words: ["Here", "is", "your"], color: "#ffffff", chip: "#2b6cf6",
    capClass: "cap-fitbox", note: "Flat colour bars · blue chip",
  },
  {
    id: "tech", name: "Tech", frame: "fill", scene: "scene-azure",
    words: ["Here", "is", "your"], color: "#bfe9ff", capClass: "cap-tech",
    note: "Edge to edge · caption centred",
  },
  {
    id: "business", name: "Business", frame: "fill", scene: "scene-warm",
    words: ["Here", "is", "your"], color: "#1e1a1a", capClass: "cap-business",
    note: "Edge to edge · white plate",
  },
  {
    id: "gameplay", name: "Gameplay", frame: "gameplay", scene: "scene-sunset",
    words: ["HERE", "IS", "YOUR"], color: "#3ad43a", capClass: "cap-gameplay",
    pop: true, note: "Half video · half gameplay",
  },
  {
    id: "podcast", name: "Podcast", frame: "podcast", scene: "scene-studio",
    words: ["Here", "is", "your"], color: "#e8f0f5", capClass: "cap-podcast",
    note: "Two-shot uncropped · room for captions",
  },
];

/** Miniature of the rendered clip: framing, caption style, and highlight timing.
 *
 * Prefers a real rendered clip from /previews/<id>.mp4 when one exists, so the
 * card shows genuine output rather than an approximation. Falls back to the CSS
 * mockup when no sample has been generated, which keeps the picker usable on a
 * fresh checkout with no footage installed. */
function TemplatePreview({ template, variants }) {
  const isSplit = template.frame === "gameplay";
  /* A podcast is a two-shot -- that IS the reason this template exists, since
     every other one zooms in far enough to lose the second person. A card
     showing one speaker would be advertising the wrong thing. */
  const isPodcast = template.frame === "podcast";
  /* Classic composites a subject band over a blurred copy of itself. The card
     used to draw one flat scene edge to edge, so it showed neither the blur nor
     the band -- which meant changing how much of the frame the video gets
     changed nothing visible here. */
  const isBlur = template.frame === "blur";
  const [hasVideo, setHasVideo] = useState(true);
  const [variant, setVariant] = useState(0);

  // Only show video when the manifest actually lists one. Guessing at a URL
  // meant a 404 per card on any install without generated previews.
  const sources = variants?.length ? variants : [];
  const showVideo = hasVideo && sources.length > 0;

  // With several gameplay loops installed, cycle them so the card shows what
  // "gameplay" actually means here rather than implying a single fixed clip.
  useEffect(() => {
    if (sources.length < 2) return;
    const id = setInterval(
      () => setVariant((v) => (v + 1) % sources.length),
      4200
    );
    return () => clearInterval(id);
  }, [sources.length]);

  return (
    <span className={`tpl-frame ${isSplit ? "is-split" : ""} ${isPodcast ? "is-podcast" : ""} ${isBlur ? "is-blur" : ""} ${template.scene}`}>
      {showVideo && (
        <video
          className="tpl-video"
          key={sources[variant]}
          src={`/previews/${sources[variant]}`}
          autoPlay
          muted
          loop
          playsInline
          preload="metadata"
          onError={() => setHasVideo(false)}
        />
      )}

      {!showVideo && (
        <>
          <span className="tpl-scene" aria-hidden="true">
            <span className="tpl-head" />
            <span className="tpl-body" />
            {isPodcast && <><span className="tpl-head two" />
                           <span className="tpl-body two" /></>}
          </span>
          {isSplit && (
            <span className="tpl-game" aria-hidden="true">
              <span className="tpl-game-lane" />
              <span className="tpl-game-lane" />
              <span className="tpl-game-player" />
            </span>
          )}
          <span
            className={`tpl-caption ${template.capClass} ${template.pop ? "pop" : ""}`}
            style={{ "--hl": template.color, "--chip": template.chip || "transparent" }}
          >
            {template.words.map((w, i) => (
              <span key={w} className="tpl-word" style={{ "--i": i }}>
                {w}
              </span>
            ))}
          </span>
        </>
      )}
    </span>
  );
}

/** One-line summary of current choices, shown while the panel is collapsed. */
function optionsSummary({ nClips, ratio, lengthPref, template, burnSubtitles }) {
  const tpl = TEMPLATES.find((t) => t.id === template);
  const lengthLabel = { any: "Any length", short: "<30s", medium: "30–60s",
                        long: "60–90s", extended: ">90s" }[lengthPref];
  return [
    `${nClips} clip${nClips > 1 ? "s" : ""}`,
    ratio,
    lengthLabel,
    burnSubtitles ? (tpl ? tpl.name : "Custom") : "No captions",
  ].join("  ·  ");
}

const MARQUEE_ITEMS = [
  "Speech-aware cuts",
  "9:16 vertical reframe",
  "Burned-in captions",
  "AI moment detection",
  "Zero editing skills",
  "Original audio kept",
  "Word-accurate subtitles",
  "One link in, clips out",
];

const HEADLINE = [
  { text: "Turn", cls: "" },
  { text: "any", cls: "" },
  { text: "video", cls: "" },
  { text: "into", cls: "" },
  { text: "clips", cls: "serif" },
  { text: "that", cls: "serif" },
  { text: "go", cls: "serif" },
  { text: "viral.", cls: "serif grad" },
];

export default function App() {
  const [url, setUrl] = useState("");
  const [nClips, setNClips] = useState(3);
  const [burnSubtitles, setBurnSubtitles] = useState(true);
  const [autoCensor, setAutoCensor] = useState(true);
  const [multilingual, setMultilingual] = useState(false);
  const [template, setTemplate] = useState("classic");
  const [ratio, setRatio] = useState("9:16");
  const [lengthPref, setLengthPref] = useState("any");
  const [intent, setIntent] = useState("");
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [editing, setEditing] = useState(null);   // { clip, index } while the editor is open
  // "idle" = landing page is just the URL bar; "setup" = the settings step.
  // Keeping choices off the landing page makes the first screen a single decision.
  const [stage, setStage] = useState("idle");
  const [showDash, setShowDash] = useState(false);
  const [studioOpen, setStudioOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const accountRef = useRef(null);

  useEffect(() => {
    if (!accountOpen) return;
    function onDown(e) {
      if (!accountRef.current?.contains(e.target)) setAccountOpen(false);
    }
    function onKey(e) {
      if (e.key === "Escape") setAccountOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [accountOpen]);
  const [user, setUser] = useState(null);
  const [authOpen, setAuthOpen] = useState(false);

  // Restores the session from a stored token; clears it if the token expired.
  useEffect(() => { fetchMe().then(setUser).catch(() => {}); }, []);

  /* Signed out means no plan, which means the free entitlement set. Read from
     the server's list rather than comparing plan names here, so adding a tier
     is a backend-only change. */
  const canMultilingual = !!user?.entitlements?.includes("multilingual");

  // Persistence makes a job reachable again after the tab is closed, so ?job=<id>
  // reopens one. Also how a shared or bookmarked result gets back on screen.
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("job");
    if (!id) return;
    getJob(id).then((j) => { setJob(j); setStudioOpen(true); }).catch(() => {});
  }, []);
  // Written by backend/make_previews.py; absent on a fresh checkout, in which
  // case each card falls back to a single <id>.mp4 and then the CSS mockup.
  const [previewManifest, setPreviewManifest] = useState({});

  useEffect(() => {
    fetch("/previews/manifest.json")
      .then((r) => (r.ok ? r.json() : {}))
      .then(setPreviewManifest)
      .catch(() => setPreviewManifest({}));
  }, []);
  const [job, setJob] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [scrolled, setScrolled] = useState(false);
  const [startedAt, setStartedAt] = useState(Date.now());
  const [progress, setProgress] = useState(0);
  const [openFaq, setOpenFaq] = useState(0);
  const pollRef = useRef(null);
  const resultsRef = useRef(null);
  const urlbarRef = useRef(null);

  const jobActive = job && !["done", "failed"].includes(job.status);

  useReveal();

  /* nav shrink + scroll progress */
  useEffect(() => {
    const onScroll = () => {
      setScrolled(window.scrollY > 24);
      const max = document.documentElement.scrollHeight - window.innerHeight;
      setProgress(max > 0 ? window.scrollY / max : 0);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    if (!job || !jobActive) return;
    pollRef.current = setInterval(async () => {
      try {
        const fresh = await getJob(job.id);
        setJob(fresh);
        if (["done", "failed"].includes(fresh.status)) {
          clearInterval(pollRef.current);
        }
      } catch {
        // transient poll failure -- keep trying
      }
    }, 1200);
    return () => clearInterval(pollRef.current);
  }, [job?.id, jobActive]);

  useEffect(() => {
    if (job?.status === "done" && resultsRef.current) {
      resultsRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [job?.status]);

  async function handleSubmit() {
    if (!url.trim() || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    setJob(null);
    setStartedAt(Date.now());
    try {
      const { job_id } = await submitJob({
        url: url.trim(),
        nClips,
        mode: "original",
        burnSubtitles,
        autoCensor,
        // Never send it on a plan that does not include it: the server would
        // reject the whole job rather than quietly downgrade it.
        multilingual: multilingual && canMultilingual,
        voice: "onyx",
        language: "English",
        template,
        ratio,
        lengthPref,
        intent: intent.trim(),
      });
      const fresh = await getJob(job_id);
      setJob(fresh);
    } catch (e) {
      setSubmitError(e.message);
    } finally {
      setSubmitting(false);
    }
  }

  function focusUrlbar() {
    urlbarRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => urlbarRef.current?.querySelector("input")?.focus(), 450);
  }

  return (
    <>
      <div className="scroll-progress" style={{ transform: `scaleX(${progress})` }} aria-hidden="true" />

      {/* ---------- Nav ---------- */}
      <header className={`nav-wrap ${scrolled ? "scrolled" : ""}`}>
        <nav className="nav-pill">
          <a className="logo" href="#top">
            <span className="logo-mark" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none">
                <path d="M6 4.5v15l13-7.5-13-7.5Z" fill="currentColor" />
              </svg>
            </span>
            Clipper
          </a>
          <div className="nav-links">
            <a href="#how">How it works</a>
            <a href="#features">Features</a>
            <a href="#faq">FAQ</a>
          </div>
          <div className="nav-cta">
            {user ? (
              /* Signed in: balance stays visible because it is the one number
                 that changes what you can do next. Everything else folds into
                 the account menu -- five separate nav buttons wrapped onto two
                 lines as soon as the bar shrank on scroll. */
              <div className="account" ref={accountRef}>
                <button
                  className="credit-pill"
                  onClick={() => setShowDash(true)}
                  title={`${user.credits} credits left`}
                >
                  <svg viewBox="0 0 24 24" width="13" height="13" fill="none" aria-hidden="true">
                    <path d="M13 2 4.5 13.5H11l-1 8.5 8.5-11.5H12l1-8.5Z" fill="currentColor" />
                  </svg>
                  {user.credits}
                </button>

                <button
                  className={`avatar ${accountOpen ? "on" : ""}`}
                  onClick={() => setAccountOpen((v) => !v)}
                  aria-haspopup="menu"
                  aria-expanded={accountOpen}
                  aria-label="Account menu"
                >
                  {user.email[0].toUpperCase()}
                </button>

                {accountOpen && (
                  <div className="account-menu" role="menu">
                    <div className="account-who">
                      <strong>{user.email}</strong>
                      <span>{user.credits} credits left</span>
                    </div>
                    <button role="menuitem" onClick={() => { setShowDash(true); setAccountOpen(false); }}>
                      My clips
                    </button>
                    <button role="menuitem" onClick={() => {
                      setAccountOpen(false);
                      document.getElementById("pricing")?.scrollIntoView({ behavior: "smooth" });
                    }}>
                      Plans &amp; credits
                    </button>
                    <button role="menuitem" className="danger" onClick={() => {
                      logout(); setUser(null); setAccountOpen(false); setShowDash(false);
                    }}>
                      Sign out
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <>
                <button className="btn btn-ghost btn-sm" onClick={() => setAuthOpen(true)}>
                  Sign in
                </button>
                <Magnetic className="btn btn-primary btn-sm" onClick={focusUrlbar} strength={0.25}>
                  Try Clipper
                </Magnetic>
              </>
            )}
          </div>
        </nav>
      </header>

      <main id="top">
        {/* ---------- Hero ---------- */}
        <section className="hero">
          <div className="hero-blobs" aria-hidden="true">
            <span className="blob b1" />
            <span className="blob b2" />
          </div>

          <div className="container hero-inner">
            <span className="hero-badge rise r0">
              <span className="badge-dot" aria-hidden="true" />
              AI clipping studio — free while in beta
            </span>

            <h1 className="hero-title" aria-label="Turn any video into clips that go viral.">
              {HEADLINE.map((w, i) => (
                <Fragment key={i}>
                  <span className="word-clip">
                    <span className={`word ${w.cls}`} style={{ animationDelay: `${0.12 + i * 0.07}s` }}>
                      {w.text}
                    </span>
                  </span>
                  {/* a real space between words, outside the clipping mask, so the
                      headline stays selectable and copies as normal text */}
                  {i < HEADLINE.length - 1 ? " " : null}
                </Fragment>
              ))}
            </h1>

            <p className="sub rise r4">
              Paste a YouTube link. Clipper finds the strongest moments, cuts on real
              speech boundaries, and renders vertical clips with word-accurate captions.
            </p>

            {/* The working element: URL bar */}
            <div className="urlbar-wrap rise r5" ref={urlbarRef}>
              <div className="urlbar-glow" aria-hidden="true" />
              <div className="urlbar">
                <svg className="urlbar-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true">
                  <path d="M10.6 13.4a4.5 4.5 0 0 0 6.36 0l3-3a4.5 4.5 0 1 0-6.36-6.36l-1.5 1.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                  <path d="M13.4 10.6a4.5 4.5 0 0 0-6.36 0l-3 3a4.5 4.5 0 1 0 6.36 6.36l1.5-1.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                </svg>
                <input
                  type="url"
                  placeholder="Paste a YouTube link..."
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
                  disabled={jobActive}
                  aria-label="YouTube video URL"
                />
                <button
                  className="btn btn-primary btn-shine"
                  onClick={() => {
                    setStudioOpen(true);
                    // A job already running means this is a way back to its
                    // progress, not a new run -- leaving it disabled stranded
                    // anyone who closed the studio mid-job.
                    if (!jobActive) { setStage("setup"); setOptionsOpen(true); }
                  }}
                  disabled={submitting || (!jobActive && !url.trim())}
                >
                  {submitting ? "Starting..." : jobActive ? "View progress" : "Continue"}
                  {!submitting && !jobActive && (
                    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" aria-hidden="true">
                      <path d="M5 12h14m0 0-6-6m6 6-6 6" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  )}
                </button>
              </div>

            </div>

            {/* Deliberately OUTSIDE .urlbar-wrap: that wrapper owns a blurred
                spinning halo sized to itself, so anything nested inside it gets
                bathed in the glow on hover. */}
            {/* Interactive clip collage */}
            <ClipFan />
          </div>
        </section>

        {/* ---------- Marquee ---------- */}
        <div className="marquee" aria-hidden="true">
          <div className="marquee-track">
            {[...MARQUEE_ITEMS, ...MARQUEE_ITEMS].map((item, i) => (
              <span className="marquee-item" key={i}>
                <span className="marquee-star">✦</span>
                {item}
              </span>
            ))}
          </div>
        </div>

        {/* ---------- Stats ---------- */}
        <section className="stats container">
          {[
            { num: <CountUp to={10} suffix="×" />, label: "Faster than manual editing" },
            { num: "9:16", label: "Vertical, ready for Shorts" },
            { num: <CountUp to={100} suffix="%" />, label: "Cuts on speech boundaries" },
            { num: "0", label: "Editing skills required" },
          ].map((s, i) => (
            <div className="stat reveal" key={i} style={{ transitionDelay: `${i * 70}ms` }}>
              <span className="stat-num">{s.num}</span>
              <span className="stat-label">{s.label}</span>
            </div>
          ))}
        </section>

        {/* ---------- How it works ---------- */}
        <section className="how container" id="how">
          <div className="section-head reveal">
            <span className="section-eyebrow">How it works</span>
            <h2 className="section-title">
              Link to clips in <em className="serif grad">four steps.</em>
            </h2>
          </div>
          <div className="how-grid">
            {[
              {
                n: "01",
                title: "Fetch & transcribe",
                body: "Pulls the source video and transcribes every word with exact timestamps.",
              },
              {
                n: "02",
                title: "Find real moments",
                body: "An AI pass picks self-contained moments — cuts snap to speech boundaries, never mid-sentence.",
              },
              {
                n: "03",
                title: "Reframe, no cropping",
                body: "Vertical 9:16 with a blurred canvas fill, so nothing in the frame gets cut off.",
              },
              {
                n: "04",
                title: "Captions burned in",
                body: "Word-accurate captions styled for short-form, rendered straight into the video.",
              },
            ].map((card, i) => (
              <div className="how-card reveal" key={card.n} style={{ transitionDelay: `${i * 80}ms` }}>
                <span className="how-num">{card.n}</span>
                <span className="how-line" aria-hidden="true" />
                <h3>{card.title}</h3>
                <p>{card.body}</p>
              </div>
            ))}
          </div>
        </section>

        {/* ---------- Features (dark band, spotlight cards) ---------- */}
        <section className="features" id="features">
          <div className="container">
            <div className="section-head reveal">
              <span className="section-eyebrow lime">Why Clipper</span>
              <h2 className="section-title light">
                Everything you need,
                <br />
                <em className="serif">nothing you don't.</em>
              </h2>
            </div>
            <div className="feature-grid">
              {[
                {
                  icon: "◉",
                  title: "Speech-aware cutting",
                  body: "Clips never start or end mid-word. Every cut lands on a natural pause.",
                },
                {
                  icon: "▥",
                  title: "Smart vertical reframe",
                  body: "Blurred canvas fill keeps the full frame visible in 9:16 — no ugly crops.",
                },
                {
                  icon: "≡",
                  title: "Word-accurate captions",
                  body: "Short-form styled captions burned into the render, timed to the word.",
                },
                {
                  icon: "◎",
                  title: "AI moment detection",
                  body: "Finds hooks, payoffs, and self-contained stories automatically.",
                },
                {
                  icon: "▸",
                  title: "Original audio kept",
                  body: "No robotic re-dubs. The real voice and energy of the source stays.",
                },
                {
                  icon: "⇣",
                  title: "One-click download",
                  body: "Every clip is a ready-to-post MP4. Download and publish anywhere.",
                },
              ].map((f, i) => (
                <SpotlightCard className="feature-card reveal" key={f.title} style={{ transitionDelay: `${(i % 3) * 80}ms` }}>
                  <span className="feature-icon" aria-hidden="true">{f.icon}</span>
                  <h3>{f.title}</h3>
                  <p>{f.body}</p>
                </SpotlightCard>
              ))}
            </div>
          </div>
        </section>

        {/* ---------- FAQ ---------- */}
        <section className="faq container" id="faq">
          <div className="section-head reveal">
            <span className="section-eyebrow">FAQ</span>
            <h2 className="section-title">
              Questions, <em className="serif grad">answered.</em>
            </h2>
          </div>
          <div className="faq-list reveal">
            {[
              {
                q: "What do I need to get started?",
                a: "Just a YouTube link. Paste it, pick how many clips you want, and hit Generate.",
              },
              {
                q: "Does it work on any video?",
                a: "It works best on talk-heavy content — podcasts, interviews, commentary, lectures. It needs speech to find the best moments.",
              },
              {
                q: "Will the cuts feel random?",
                a: "No. Every clip is chosen by an AI pass over the full transcript and snapped to real speech boundaries, so clips are self-contained.",
              },
              {
                q: "What format are the clips?",
                a: "Vertical 9:16 MP4s with burned-in captions — ready for Shorts, Reels, and TikTok.",
              },
            ].map((item, i) => (
              <div className={`faq-item ${openFaq === i ? "open" : ""}`} key={item.q}>
                <button
                  className="faq-q"
                  onClick={() => setOpenFaq(openFaq === i ? -1 : i)}
                  aria-expanded={openFaq === i}
                >
                  {item.q}
                  <span className="faq-plus" aria-hidden="true">+</span>
                </button>
                <div className="faq-a-wrap">
                  <div className="faq-a">
                    <p>{item.a}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ---------- Pricing ---------- */}
        <Pricing user={user} onRequireAuth={() => setAuthOpen(true)} />

        {/* ---------- Big CTA ---------- */}
        <section className="cta-band">
          <div className="container cta-inner reveal">
            <h2>
              Ready to clip your
              <br />
              <em className="serif">first video?</em>
            </h2>
            <p>Paste a link and get vertical, captioned clips in minutes.</p>
            <Magnetic className="btn btn-white btn-lg btn-shine" onClick={focusUrlbar}>
              Try Clipper — it's free
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" aria-hidden="true">
                <path d="M5 12h14m0 0-6-6m6 6-6 6" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </Magnetic>
          </div>
        </section>
      </main>

      <footer className="footer">
        <div className="container footer-inner">
          <span className="logo small">
            <span className="logo-mark" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none">
                <path d="M6 4.5v15l13-7.5-13-7.5Z" fill="currentColor" />
              </svg>
            </span>
            Clipper
          </span>
          <span className="footer-note">FastAPI · React · ffmpeg · Deepgram</span>
        </div>
      </footer>


      {/* ---------- Studio: the whole make-a-clip flow, off the landing page ---------- */}
      {studioOpen && (
        <div className="workspace">
          <div className="container">
            <header className="dash-head">
              <div>
                <h2>Studio</h2>
                <p className="dash-sub">Paste a link. Everything else happens here.</p>
              </div>
              <button className="btn btn-ghost btn-sm" onClick={() => setStudioOpen(false)}>
                Exit studio
              </button>
            </header>

            {!job && (
              <div className="workspace-compose">
                <div className="workspace-urlrow">
                  <input
                    type="url"
                    placeholder="Paste a YouTube link..."
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && url.trim()) { setStage("setup"); setOptionsOpen(true); }
                    }}
                    aria-label="YouTube video URL"
                  />
                  {stage !== "setup" && (
                    <button
                      className="btn btn-primary btn-sm btn-shine"
                      disabled={!url.trim()}
                      onClick={() => { setStage("setup"); setOptionsOpen(true); }}
                    >
                      Continue
                    </button>
                  )}
                </div>
              </div>
            )}

            {stage === "setup" && !jobActive && (
            <div className="composer-options">
              <button
                type="button"
                className={`options-toggle ${optionsOpen ? "open" : ""}`}
                onClick={() => setOptionsOpen((v) => !v)}
                aria-expanded={optionsOpen}
              >
                <span className="options-toggle-label">Output settings</span>
                <span className="options-toggle-summary">
                  {optionsSummary({ nClips, ratio, lengthPref, template, burnSubtitles })}
                </span>
                <svg className="options-chevron" viewBox="0 0 24 24" width="16" height="16"
                     fill="none" aria-hidden="true">
                  <path d="m6 9 6 6 6-6" stroke="currentColor" strokeWidth="2.4"
                        strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>

              {optionsOpen && (
                <div className="options-body">
              <div className="options-row">
                <label className="chip">
                  Clips
                  <select
                    value={nClips}
                    onChange={(e) => setNClips(Number(e.target.value))}
                    disabled={jobActive}
                  >
                    {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => (
                      <option key={n} value={n}>{n}</option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  className={`chip ${burnSubtitles ? "active" : ""}`}
                  onClick={() => setBurnSubtitles((v) => !v)}
                  disabled={jobActive}
                >
                  <span className="chip-check" aria-hidden="true">{burnSubtitles ? "✓" : ""}</span>
                  Captions
                </button>
                <button
                  type="button"
                  className={`chip ${autoCensor ? "active" : ""}`}
                  onClick={() => setAutoCensor((v) => !v)}
                  disabled={jobActive}
                  title="Mutes profanity and stars it in captions, so clips stay monetisable"
                >
                  <span className="chip-check" aria-hidden="true">{autoCensor ? "✓" : ""}</span>
                  Auto-censor
                </button>
                {/* Shown to everyone, locked for plans that do not include it.
                    Hiding it entirely would mean the people most likely to
                    want it -- creators whose footage is bilingual -- never
                    learn it exists. The backend gates this independently;
                    `canMultilingual` is presentation only. */}
                <button
                  type="button"
                  className={`chip ${multilingual ? "active" : ""} ${canMultilingual ? "" : "locked"}`}
                  onClick={() => canMultilingual
                    ? setMultilingual((v) => !v)
                    : document.getElementById("pricing")
                        ?.scrollIntoView({ behavior: "smooth" })}
                  disabled={jobActive}
                  title={canMultilingual
                    ? (user?.is_admin
                        ? "Unlocked by your admin account — transcribes speech that switches language mid-sentence"
                        : "Transcribes speech that switches language mid-sentence, like Hindi and English in one line")
                    : "Creator and Pro plans — for speech that switches language mid-sentence"}
                >
                  <span className="chip-check" aria-hidden="true">
                    {canMultilingual ? (multilingual ? "✓" : "") : "🔒"}
                  </span>
                  Mixed language
                </button>
                <label className="chip">
                  Ratio
                  <select
                    value={ratio}
                    onChange={(e) => setRatio(e.target.value)}
                    disabled={jobActive}
                  >
                    <option value="9:16">9:16</option>
                    <option value="1:1">1:1</option>
                    <option value="16:9">16:9</option>
                  </select>
                </label>
                <label className="chip">
                  Length
                  <select
                    value={lengthPref}
                    onChange={(e) => setLengthPref(e.target.value)}
                    disabled={jobActive}
                  >
                    <option value="any">Any length</option>
                    <option value="short">&lt;30s</option>
                    <option value="medium">30–60s</option>
                    <option value="long">60–90s</option>
                    <option value="extended">&gt;90s</option>
                  </select>
                </label>
              </div>

              <div className="template-picker">
                <div className="template-picker-label">Template</div>
                <div className="template-grid">
                  {TEMPLATES.map((t) => {
                    const variants = previewManifest[t.id];
                    const selected = template === t.id;
                    return (
                      <button
                        type="button"
                        key={t.id}
                        className={`template-card ${selected ? "selected" : ""}`}
                        onClick={() => setTemplate(t.id)}
                        disabled={jobActive}
                        aria-pressed={selected}
                      >
                        <TemplatePreview template={t} variants={variants} />
                        <span className="template-name">{t.name}</span>
                        <span className="template-note">{t.note}</span>
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
                <input
                  type="text"
                  value={intent}
                  onChange={(e) => setIntent(e.target.value)}
                  placeholder="For example: when he talks about pricing."
                  disabled={jobActive}
                />
              </label>
                </div>
              )}

              {submitError && <div className="error-box">{submitError}</div>}

              <div className="setup-actions">
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => setStage("idle")}
                  disabled={submitting}
                >
                  Back
                </button>
                <button
                  className="btn btn-primary btn-shine"
                  onClick={handleSubmit}
                  disabled={submitting || jobActive}
                >
                  {submitting ? "Starting..." : "Generate clips"}
                </button>
              </div>
            </div>
            )}

            {/* Working panel */}
            {job && job.status !== "done" && (
              <ProcessingStudio job={job} startedAt={startedAt} onCancel={() => setJob(null)} />
            )}
          </div>

        {/* ---------- Results ---------- */}
        {job?.status === "done" && (
          <section className="results container" ref={resultsRef}>
            <div className="results-head">
              <span className="results-badge">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" aria-hidden="true">
                  <path d="m5 13 4 4L19 7" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                {job.clips.length} clip{job.clips.length === 1 ? "" : "s"} ready
              </span>
              <h2 className="section-title">
                Fresh out of <em className="serif grad">the studio.</em>
              </h2>
              <p className="section-sub">
                Hover any clip to preview it. Every cut lands on a real speech boundary.
              </p>
            </div>

            <div className="clip-grid">
              {job.clips.map((clip, i) => (
                <article className="clip-card" key={clip.file} style={{ animationDelay: `${i * 110}ms` }}>
                  <div className="clip-stage">
                    <video
                      className="clip-video"
                      src={clipUrl(job.id, clip.file, clip.version)}
                      key={clip.version}
                      controls
                      playsInline
                      preload="metadata"
                      onMouseEnter={(e) => {
                        e.currentTarget.play().catch(() => {});
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.pause();
                        e.currentTarget.currentTime = 0;
                      }}
                    />
                    <span className="clip-index">#{i + 1}</span>
                    <span className="clip-duration">{fmtDuration(clip.end - clip.start)}</span>
                  </div>
                  <div className="clip-meta">
                    {/* The score is why you would pick this clip, so it sits
                        beside the title rather than under six progress bars. */}
                    <div className="clip-headline">
                      <div>
                        <h3 className="clip-title">{clip.title}</h3>
                        <p className="clip-hook">{clip.hook}</p>
                      </div>
                      {clip.score != null && (
                        <div className="clip-score-badge" title="Virality score">
                          <strong>{clip.score.toFixed(1)}</strong>
                          <span>Virality</span>
                        </div>
                      )}
                    </div>

                    {clip.scores && (
                      <div className="clip-scores">
                        <div className="scores-head">
                          <span>Why this one</span>
                        </div>
                        {/* Must track analyzer.WEIGHTS -- a key that no longer
                            exists renders as a silently empty bar. */}
                        {[
                          ["Hook", "hook"],
                          ["Stands alone", "standalone"],
                          ["Emotion", "emotion"],
                          ["Ending", "ending"],
                          ["Payoff", "payoff"],
                          ["Shareable", "share"],
                        ]
                          .filter(([, key]) => clip.scores?.[key] != null)
                          .map(([label, key]) => (
                            <div className="score-row" key={key}>
                              <span className="score-label">{label}</span>
                              <span className="score-bar">
                                <i style={{ width: `${Math.min(Number(clip.scores[key]) || 0, 10) * 10}%` }} />
                              </span>
                            </div>
                          ))}
                      </div>
                    )}
                    <div className="clip-actions">
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm clip-edit"
                        onClick={() => setEditing({ clip, index: i })}
                        title="Trim or extend this clip"
                      >
                        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" aria-hidden="true">
                          <path d="M4 8h16M4 16h16M9 5v6M15 13v6" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
                        </svg>
                        Trim
                      </button>
                      <a
                        className="btn btn-primary btn-shine clip-download"
                        href={clipUrl(job.id, clip.file, clip.version)}
                        download={downloadName(clip.title, i)}
                      >
                        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" aria-hidden="true">
                          <path d="M12 4v11m0 0 4-4m-4 4-4-4M5 19h14" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                        Download
                      </a>
                    </div>
                  </div>
                </article>
              ))}
            </div>

            {editing && (
              <LiveEditor
                job={job}
                clip={editing.clip}
                index={editing.index}
                onClose={() => setEditing(null)}
                onSaved={(index, updated) =>
                  setJob((prev) => {
                    if (!prev) return prev;
                    const clips = [...prev.clips];
                    // cache-bust: the filename is unchanged but the bytes are not
                    clips[index] = { ...clips[index], ...updated, _v: Date.now() };
                    return { ...prev, clips };
                  })
                }
              />
            )}

            <button
              className="btn btn-ghost results-again"
              onClick={() => {
                setJob(null);
                setUrl("");
                focusUrlbar();
              }}
            >
              Clip another video
            </button>
          </section>
        )}
        </div>
      )}

      {showDash && user && (
        <Dashboard
          user={user}
          onClose={() => { setShowDash(false); setStage("idle"); }}
          onOpenJob={(j) => { setJob(j); setShowDash(false); setStudioOpen(true); }}
        />
      )}

      {authOpen && (
        <AuthModal
          onClose={() => setAuthOpen(false)}
          onAuthed={setUser}
          initialMode="signup"
        />
      )}
    </>
  );
}
