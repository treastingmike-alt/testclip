import { useEffect, useMemo, useRef, useState } from "react";

const API_BASE = "/api";

function Glyph({ children }) {
  return <span className="glyph" aria-hidden="true">{children}</span>;
}

const STAGES = [
  { key: "queued", label: "Queued", copy: "Your video is entering the clipping lane." },
  { key: "downloading", label: "Fetching source", copy: "Pulling the source and extracting clean audio." },
  { key: "transcribing", label: "Reading every word", copy: "Building word-level timing for smart cuts and captions." },
  { key: "analyzing", label: "Finding moments", copy: "Scoring hooks, payoff, emotion, and standalone value." },
  { key: "rendering", label: "Rendering clips", copy: "Reframing, styling captions, and exporting MP4s." },
  { key: "done", label: "Ready", copy: "Your clips are ready to preview and download." },
];

const TEMPLATES = [
  { id: "classic", name: "Classic", meta: "Blur fill, punchy captions" },
  { id: "fitvideo", name: "Fit Video", meta: "Full frame, clean bars" },
  { id: "tech", name: "Tech", meta: "Edge-to-edge, crisp highlight" },
  { id: "business", name: "Business", meta: "Editorial, brand-safe" },
  { id: "gameplay", name: "Gameplay", meta: "Split clip plus gameplay" },
  { id: "podcast", name: "Podcast", meta: "Two speakers kept visible" },
];

const FEATURES = [
  {
    mark: "//",
    title: "Speech-boundary cuts",
    copy: "Clipper does not guess timestamps. It chooses transcript moments, then snaps starts and ends to real speech boundaries.",
  },
  {
    mark: "CC",
    title: "Word-accurate captions",
    copy: "Captions are rendered into the MP4, styled for Reels, Shorts, and TikTok with timing that follows the speaker.",
  },
  {
    mark: "AI",
    title: "AI viral scoring",
    copy: "Every result is ranked by hook strength, payoff, emotion, shareability, and whether the clip stands alone.",
  },
  {
    mark: "OK",
    title: "Auto-censoring",
    copy: "Profanity can be muted and starred in captions so clips stay safer for platforms and brand channels.",
  },
  {
    mark: "A+",
    title: "Mixed-language aware",
    copy: "Built for creators who switch language mid-thought, so multilingual speech can still become usable shorts.",
  },
  {
    mark: "!!",
    title: "Original energy kept",
    copy: "The real voice, pace, and delivery stay intact. AI finds the clip; it does not flatten the performance.",
  },
];

const FAQS = [
  {
    q: "What type of videos work best?",
    a: "Podcasts, interviews, courses, commentary, webinars, sermons, and any talk-heavy video where the best moments are in the speech.",
  },
  {
    q: "Can this connect to my current backend?",
    a: "Yes. This concept uses the same /api/jobs contract as the existing frontend, including polling and clip downloads.",
  },
  {
    q: "Will clips be cropped badly?",
    a: "The existing renderer can preserve the original frame with a smart vertical treatment, and templates decide how aggressive the framing should be.",
  },
  {
    q: "Is this replacing the current app?",
    a: "No. It lives in a separate folder so you can compare it safely before deciding what should move into production.",
  },
];

function authHeaders() {
  const token = localStorage.getItem("clipper_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function readError(resp, fallback) {
  try {
    const body = await resp.json();
    const detail = Array.isArray(body.detail)
      ? body.detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
      : body.detail;
    return new Error(detail || fallback);
  } catch {
    return new Error(fallback);
  }
}

async function submitJob(payload) {
  const resp = await fetch(`${API_BASE}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      url: payload.url,
      n_clips: payload.nClips,
      mode: "original",
      burn_subtitles: payload.burnSubtitles,
      auto_censor: payload.autoCensor,
      multilingual: payload.multilingual,
      voice: "onyx",
      language: "English",
      template: payload.template,
      ratio: payload.ratio,
      length_pref: payload.lengthPref,
      intent: payload.intent,
    }),
  });
  if (!resp.ok) throw await readError(resp, `Failed to start clipping (${resp.status})`);
  return resp.json();
}

async function getJob(jobId) {
  const resp = await fetch(`${API_BASE}/jobs/${jobId}`);
  if (!resp.ok) throw await readError(resp, `Could not load job (${resp.status})`);
  return resp.json();
}

function clipUrl(jobId, filename, version) {
  const base = `${API_BASE}/clips/${jobId}/${filename}`;
  return version ? `${base}?v=${version}` : base;
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function useReveal() {
  useEffect(() => {
    const items = document.querySelectorAll("[data-reveal]");
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.16 }
    );

    items.forEach((item) => observer.observe(item));
    return () => observer.disconnect();
  }, []);
}

function useScrollProgress() {
  const [progress, setProgress] = useState(0);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      setProgress(max > 0 ? window.scrollY / max : 0);
      setScrolled(window.scrollY > 20);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return { progress, scrolled };
}

function StageDemo() {
  return (
    <div className="stage-demo" aria-hidden="true">
      <div className="source-film">
        <span className="source-label">2h 14m source</span>
        {Array.from({ length: 9 }).map((_, index) => (
          <span key={index} className="frame-slice" />
        ))}
        <span className="scan-line" />
      </div>

      <div className="clip-output-row">
        {["Hook", "Payoff", "Story"].map((label, index) => (
          <div className={`mini-reel reel-${index + 1}`} key={label}>
            <span className="reel-top">
              <Glyph>{">"}</Glyph>
              {label}
            </span>
            <span className="reel-caption">viral clip {index + 1}</span>
            <span className="reel-score">{[9.2, 8.8, 8.4][index]}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Hero({ url, setUrl, onStart }) {
  return (
    <section className="hero" id="top">
      <img className="hero-media" src="/assets/hero-studio.png" alt="" />
      <div className="hero-scrim" />
      <div className="hero-grid" aria-hidden="true" />

      <div className="hero-content">
        <span className="eyebrow hero-eyebrow">
          <Glyph>*</Glyph>
          AI clipping studio for creators and brands
        </span>
        <h1>AI clipping studio for long videos</h1>
        <p className="hero-copy">
          Paste a link, choose the style, and let Clipper turn long-form videos into
          ranked, captioned, ready-to-post vertical clips.
        </p>

        <form
          className="hero-url"
          onSubmit={(event) => {
            event.preventDefault();
            onStart();
          }}
        >
          <Glyph>link</Glyph>
          <input
            type="url"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="Paste a YouTube link..."
            aria-label="Video URL"
          />
          <button className="primary-action" type="submit" disabled={!url.trim()}>
            Start clipping
            <Glyph>{"->"}</Glyph>
          </button>
        </form>

        <div className="hero-metrics" aria-label="Product highlights">
          <span><strong>10x</strong> faster output</span>
          <span><strong>9:16</strong> Shorts-ready</span>
          <span><strong>AI</strong> hook scoring</span>
        </div>
      </div>

      <StageDemo />
    </section>
  );
}

function Nav({ scrolled, onStart }) {
  return (
    <header className={`site-nav ${scrolled ? "is-scrolled" : ""}`}>
      <a className="brand" href="#top" aria-label="Clipper home">
        <span className="brand-mark"><Glyph>//</Glyph></span>
        Clipper
      </a>
      <nav className="nav-links" aria-label="Primary navigation">
        <a href="#how">How it works</a>
        <a href="#features">Features</a>
        <a href="#studio">Studio</a>
        <a href="#pricing">Pricing</a>
      </nav>
      <button className="nav-button" onClick={onStart}>
        Try it
        <Glyph>{"->"}</Glyph>
      </button>
    </header>
  );
}

function SectionIntro({ eyebrow, title, copy, light = false }) {
  return (
    <div className={`section-intro ${light ? "light" : ""}`} data-reveal>
      <span className="eyebrow">{eyebrow}</span>
      <h2>{title}</h2>
      {copy && <p>{copy}</p>}
    </div>
  );
}

function WorkFlow() {
  const steps = [
    ["01", "Drop a source", "Paste a YouTube link and choose how many clips you want."],
    ["02", "AI reads the full story", "The transcript is analyzed for hooks, emotion, payoff, and clean context."],
    ["03", "Templates shape the clip", "Pick caption and framing styles made for vertical platforms."],
    ["04", "Download the winners", "Preview ranked clips, trim when needed, and export ready-to-post MP4s."],
  ];

  return (
    <section className="workflow section-pad" id="how">
      <SectionIntro
        eyebrow="How it works"
        title="From long video to short-form output in one calm flow."
        copy="The page explains the process visually while the Studio keeps the actual job controls close by."
      />
      <div className="timeline-path">
        {steps.map(([num, title, copy], index) => (
          <article className="step-card" key={num} data-reveal style={{ transitionDelay: `${index * 80}ms` }}>
            <span>{num}</span>
            <h3>{title}</h3>
            <p>{copy}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function FeatureMatrix() {
  return (
    <section className="feature-band section-pad" id="features">
      <SectionIntro
        eyebrow="Why it convinces"
        title="A website that sells the outcome, not just the technology."
        copy="Every section ties a feature to the creator benefit: less editing, stronger clips, cleaner publishing."
        light
      />
      <div className="feature-grid">
        {FEATURES.map((feature, index) => {
          return (
            <article className="feature-card" key={feature.title} data-reveal style={{ transitionDelay: `${(index % 3) * 70}ms` }}>
              <span className="feature-icon"><Glyph>{feature.mark}</Glyph></span>
              <h3>{feature.title}</h3>
              <p>{feature.copy}</p>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function ProductShowcase() {
  return (
    <section className="showcase section-pad">
      <div className="showcase-copy" data-reveal>
        <span className="eyebrow">Product walkthrough</span>
        <h2>Users see exactly what is happening before they pay.</h2>
        <p>
          The design makes the AI process tangible: transcript analysis, moment
          scoring, caption templates, and finished reels are all visible instead of hidden
          behind a vague generate button.
        </p>
      </div>

      <div className="showcase-visual" data-reveal>
        <div className="analysis-panel">
          <div className="panel-top">
            <span>Transcript intelligence</span>
            <Glyph>ok</Glyph>
          </div>
          {[
            ["Hook density", 92],
            ["Standalone story", 86],
            ["Emotional lift", 78],
            ["Payoff clarity", 89],
          ].map(([label, value]) => (
            <div className="signal-row" key={label}>
              <span>{label}</span>
              <i><b style={{ width: `${value}%` }} /></i>
              <strong>{value}</strong>
            </div>
          ))}
        </div>
        <div className="phone-stack">
          <div className="phone phone-main">
            <span className="phone-caption">THIS IS THE MOMENT</span>
            <span className="phone-score">9.3</span>
          </div>
          <div className="phone phone-back" />
        </div>
      </div>
    </section>
  );
}

function Studio({ initialUrl = "" }) {
  const [url, setUrl] = useState(initialUrl);
  const [nClips, setNClips] = useState(3);
  const [burnSubtitles, setBurnSubtitles] = useState(true);
  const [autoCensor, setAutoCensor] = useState(true);
  const [multilingual, setMultilingual] = useState(false);
  const [ratio, setRatio] = useState("9:16");
  const [lengthPref, setLengthPref] = useState("any");
  const [template, setTemplate] = useState("classic");
  const [intent, setIntent] = useState("");
  const [job, setJob] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const pollRef = useRef(null);

  const activeStage = useMemo(() => {
    if (!job) return STAGES[0];
    return STAGES.find((stage) => stage.key === job.status) || STAGES[0];
  }, [job]);

  const activeIndex = useMemo(() => {
    if (!job) return 0;
    return Math.max(0, STAGES.findIndex((stage) => stage.key === job.status));
  }, [job]);

  const isWorking = job && !["done", "failed"].includes(job.status);

  useEffect(() => {
    if (initialUrl && !url) setUrl(initialUrl);
  }, [initialUrl, url]);

  useEffect(() => {
    if (!job?.id || !isWorking) return undefined;
    pollRef.current = setInterval(async () => {
      try {
        const fresh = await getJob(job.id);
        setJob(fresh);
        if (["done", "failed"].includes(fresh.status)) clearInterval(pollRef.current);
      } catch {
        // keep polling through transient backend restarts
      }
    }, 1300);
    return () => clearInterval(pollRef.current);
  }, [job?.id, isWorking]);

  async function start() {
    if (!url.trim() || submitting) return;
    setSubmitting(true);
    setError("");
    setJob(null);
    try {
      const result = await submitJob({
        url: url.trim(),
        nClips,
        burnSubtitles,
        autoCensor,
        multilingual,
        template,
        ratio,
        lengthPref,
        intent: intent.trim(),
      });
      setJob(await getJob(result.job_id));
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="studio-section section-pad" id="studio">
      <SectionIntro
        eyebrow="Live concept"
        title="A premium studio panel that still talks to your real backend."
        copy="Use this page as a safe side-by-side prototype. The existing frontend remains untouched."
      />

      <div className="studio-shell" data-reveal>
        <div className="studio-compose">
          <div className="studio-header">
            <div>
              <span className="eyebrow">Create</span>
              <h3>Generate viral clips</h3>
            </div>
            <span className="credit-pill"><Glyph>!!</Glyph> Source length free</span>
          </div>

          <label className="field-label">
            Video URL
            <span className="input-line">
              <Glyph>link</Glyph>
              <input
                type="url"
                placeholder="https://youtube.com/watch..."
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                disabled={isWorking || submitting}
              />
            </span>
          </label>

          <div className="control-grid">
            <label>
              Clips
              <select value={nClips} onChange={(event) => setNClips(Number(event.target.value))} disabled={isWorking}>
                {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((count) => (
                  <option value={count} key={count}>{count}</option>
                ))}
              </select>
            </label>
            <label>
              Ratio
              <select value={ratio} onChange={(event) => setRatio(event.target.value)} disabled={isWorking}>
                <option value="9:16">9:16</option>
                <option value="1:1">1:1</option>
                <option value="16:9">16:9</option>
              </select>
            </label>
            <label>
              Length
              <select value={lengthPref} onChange={(event) => setLengthPref(event.target.value)} disabled={isWorking}>
                <option value="any">Any length</option>
                <option value="short">&lt;30s</option>
                <option value="medium">30-60s</option>
                <option value="long">60-90s</option>
                <option value="extended">&gt;90s</option>
              </select>
            </label>
          </div>

          <div className="toggle-row">
            {[
              [burnSubtitles, setBurnSubtitles, "CC", "Captions"],
              [autoCensor, setAutoCensor, "OK", "Auto-censor"],
              [multilingual, setMultilingual, "A+", "Mixed language"],
            ].map(([value, setter, mark, label]) => (
              <button
                type="button"
                className={`toggle-chip ${value ? "active" : ""}`}
                onClick={() => setter(!value)}
                disabled={isWorking}
                key={label}
              >
                <Glyph>{mark}</Glyph>
                {label}
                {value && <Glyph>yes</Glyph>}
              </button>
            ))}
          </div>

          <div className="template-strip">
            {TEMPLATES.map((item) => (
              <button
                type="button"
                className={`template-pill ${template === item.id ? "active" : ""}`}
                onClick={() => setTemplate(item.id)}
                disabled={isWorking}
                key={item.id}
              >
                <span>{item.name}</span>
                <small>{item.meta}</small>
              </button>
            ))}
          </div>

          <label className="field-label">
            Target moment <em>optional</em>
            <input
              className="plain-input"
              value={intent}
              onChange={(event) => setIntent(event.target.value)}
              placeholder="Example: when the guest explains pricing"
              disabled={isWorking}
            />
          </label>

          {error && <div className="studio-error">{error}</div>}

          <button className="primary-action studio-action" onClick={start} disabled={!url.trim() || submitting || isWorking}>
            {submitting ? "Starting..." : "Generate clips"}
            <Glyph>up</Glyph>
          </button>
        </div>

        <div className="studio-status">
          <div className="status-top">
            <span className="eyebrow">Pipeline</span>
            <strong>{job?.status === "failed" ? "Needs attention" : activeStage.label}</strong>
            <p>{job?.status === "failed" ? job.error || "The backend could not complete this video." : job?.progress_message || activeStage.copy}</p>
          </div>

          <div className="stage-list">
            {STAGES.slice(0, -1).map((stage, index) => (
              <div className={`stage-row ${index <= activeIndex ? "active" : ""}`} key={stage.key}>
                <span>{index < activeIndex || job?.status === "done" ? <Glyph>ok</Glyph> : index + 1}</span>
                <div>
                  <strong>{stage.label}</strong>
                  <small>{stage.copy}</small>
                </div>
              </div>
            ))}
          </div>

          <div className="progress-meter">
            <span style={{ width: `${job?.status === "done" ? 100 : Math.min(98, Math.max(0, job?.percent || activeIndex * 18))}%` }} />
          </div>

          {!job && (
            <div className="empty-output">
              <Glyph>set</Glyph>
              <p>Your job progress and finished clip cards will appear here.</p>
            </div>
          )}
        </div>
      </div>

      {job?.status === "done" && (
        <div className="results-grid" data-reveal>
          {job.clips.map((clip, index) => (
            <article className="result-card" key={clip.file}>
              <div className="result-video">
                <video src={clipUrl(job.id, clip.file, clip.version)} controls playsInline preload="metadata" />
                <span>#{index + 1}</span>
                <b>{formatDuration(clip.duration || clip.end - clip.start)}</b>
              </div>
              <div className="result-copy">
                <h3>{clip.title || `Clip ${index + 1}`}</h3>
                <p>{clip.hook || "Ready to review, download, and publish."}</p>
                <div className="result-footer">
                  {clip.score != null && <span>{Number(clip.score).toFixed(1)} virality</span>}
                  <a className="download-link" href={clipUrl(job.id, clip.file, clip.version)} download>
                    Download
                    <Glyph>dl</Glyph>
                  </a>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function Pricing() {
  return (
    <section className="pricing section-pad" id="pricing">
      <SectionIntro
        eyebrow="Money story"
        title="Make pricing feel fair before the checkout page."
        copy="The page keeps the core promise visible: users pay for finished clips, not for every minute of source footage."
      />
      <div className="pricing-grid">
        {[
          ["Starter", "$0", "Test the workflow", ["Free clips on signup", "YouTube links", "Caption templates"]],
          ["Creator", "$19", "For consistent posting", ["More monthly credits", "Mixed-language support", "Priority rendering"]],
          ["Studio", "$59", "For teams and agencies", ["Brand-safe templates", "Team workspace ready", "Workflow automation path"]],
        ].map(([name, price, copy, items], index) => (
          <article className={`price-card ${index === 1 ? "featured" : ""}`} key={name} data-reveal>
            {index === 1 && <span className="price-badge">Most popular</span>}
            <h3>{name}</h3>
            <p>{copy}</p>
            <strong>{price}<small>/mo</small></strong>
            <ul>
              {items.map((item) => (
                <li key={item}><Glyph>ok</Glyph> {item}</li>
              ))}
            </ul>
            <a href="#studio" className={index === 1 ? "primary-action" : "secondary-action"}>
              Choose {name}
              <Glyph>{"->"}</Glyph>
            </a>
          </article>
        ))}
      </div>
    </section>
  );
}

function FAQ() {
  const [open, setOpen] = useState(0);
  return (
    <section className="faq section-pad">
      <SectionIntro eyebrow="FAQ" title="Clear enough for first-time buyers." />
      <div className="faq-list" data-reveal>
        {FAQS.map((item, index) => (
          <article className={`faq-item ${open === index ? "open" : ""}`} key={item.q}>
            <button onClick={() => setOpen(open === index ? -1 : index)} aria-expanded={open === index}>
              {item.q}
              <Glyph>v</Glyph>
            </button>
            <div className="faq-answer"><p>{item.a}</p></div>
          </article>
        ))}
      </div>
    </section>
  );
}

export default function App() {
  const [heroUrl, setHeroUrl] = useState("");
  const { progress, scrolled } = useScrollProgress();
  useReveal();

  function jumpToStudio() {
    document.getElementById("studio")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <>
      <div className="scroll-progress" style={{ transform: `scaleX(${progress})` }} />
      <Nav scrolled={scrolled} onStart={jumpToStudio} />
      <main>
        <Hero url={heroUrl} setUrl={setHeroUrl} onStart={jumpToStudio} />
        <section className="proof-strip" aria-label="Use cases">
          {["Podcasts", "Courses", "Webinars", "YouTube lives", "Founder videos", "Sports talk"].map((item) => (
            <span key={item}>{item}</span>
          ))}
        </section>
        <WorkFlow />
        <FeatureMatrix />
        <ProductShowcase />
        <Studio initialUrl={heroUrl} />
        <Pricing />
        <FAQ />
        <section className="final-cta">
          <span className="eyebrow">Ready for the comparison?</span>
          <h2>Paste one long video. Walk away with a week of clips.</h2>
          <a className="primary-action" href="#studio">
            Open the studio
            <Glyph>go</Glyph>
          </a>
        </section>
      </main>
      <footer className="site-footer">
        <span className="brand"><span className="brand-mark"><Glyph>//</Glyph></span>Clipper</span>
        <span>Premium frontend concept. Existing app untouched.</span>
      </footer>
    </>
  );
}
