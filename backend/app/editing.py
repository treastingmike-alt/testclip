"""Re-cutting an already-rendered clip to new in/out points.

The whole pipeline already produces what an editor needs -- word-level timings,
sentence boundaries, the chosen template -- it was just being thrown away after
the first render. This module reuses the stored transcript so a boundary change
costs one ffmpeg pass and nothing else: no re-download of audio, no second
Deepgram bill, no re-running the LLM passes.

The one real cost is the source video. It is deleted after the first render
because it is by far the largest thing on disk, so an edit re-fetches it when
missing. That is a deliberate trade: a few minutes on the rare edit, against
hundreds of MB per job sitting around forever on the common path.
"""

import os

from app.db import SessionLocal
from app.models import Clip, Job
from app.pipeline import censor, downloader, render, subtitles, transcriber, translation

# A trim must still leave something watchable, and an extend must not run away
# with the whole source video.
MIN_EDIT_SECONDS = 3.0
MAX_EDIT_SECONDS = 600.0


def words_in_range(transcript: dict, start: float, end: float) -> list:
    """Word objects whose midpoint falls inside [start, end].

    Midpoint rather than full containment: a word straddling the boundary should
    belong to whichever side it mostly sits in, otherwise trimming by a tenth of
    a second can silently drop a word from the captions.
    """
    words = []
    for utt in transcriber.get_utterances(transcript):
        for w in utt.get("words", []):
            mid = (w["start"] + w["end"]) / 2
            if start <= mid <= end:
                words.append(w)
    return words


def ensure_source(job: Job, job_dir: str, on_progress=None) -> str:
    """Returns a path to the source video, re-downloading it if it was cleaned."""
    path = os.path.join(job_dir, "source.mp4")
    if os.path.exists(path):
        return path
    os.makedirs(job_dir, exist_ok=True)
    return downloader.download_video(job.url, path, on_progress=on_progress)


def words_from_lines(lines: list, transcript: dict = None) -> list:
    """Rebuilds word timings from caption lines, keeping real timings where possible.

    A line is {"start", "end", "text", "edited"}. Only a REWRITTEN line loses
    its per-word timings -- its words are different words, so the new tokens are
    spread evenly across the span. Karaoke on those lines becomes even rather
    than exact: the honest trade for letting text be freely rewritten.

    Lines the user did not touch keep the transcript's real word timings. This
    matters because editing one line used to re-spread EVERY line in the clip,
    so fixing a single typo quietly degraded the karaoke timing of the whole
    caption track.
    """
    original = []
    if transcript is not None:
        for utt in transcriber.get_utterances(transcript):
            original.extend(utt.get("words", []))

    words = []
    for line in lines:
        tokens = [t for t in (line.get("text") or "").split() if t]
        if not tokens:
            continue
        span_start, span_end = float(line["start"]), float(line["end"])

        if not line.get("edited", True) and original:
            # Untouched: take the real words that sit inside this span. Midpoint
            # containment, matching words_in_range, so a word on the boundary
            # lands on the side it mostly occupies.
            real = [w for w in original
                    if span_start <= (w["start"] + w["end"]) / 2 <= span_end]
            if real:
                words.extend(real)
                continue
            # No real words found (a re-timed span): fall through and spread.

        dt = (span_end - span_start) / len(tokens)
        for k, tok in enumerate(tokens):
            t0 = span_start + k * dt
            words.append({
                "word": tok.strip(".,!?"),
                "punctuated_word": tok,
                "start": round(t0, 3),
                "end": round(t0 + dt * 0.85, 3),
            })

    # Cues are grouped in order downstream, and a mixed track can interleave.
    words.sort(key=lambda w: w["start"])
    return words


def rerender_clip(job_id: str, index: int, start: float, end: float,
                  storage_dir: str, templates: dict, gameplay_loops: list = None,
                  caption_style: str = None, caption_lines: list = None,
                  caption_font: str = None, translate_to: str = None,
                  ratio: str = None, overlay_list: list = None,
                  caption_size: int = None, caption_pos: float = None,
                  caption_anim: str = None, speed: float = None,
                  speed_pitched: bool = False, background: str = None,
                  title_style: str = None, title_font: str = None):
    """Re-cuts clip `index` of `job_id` to [start, end]. Returns the updated clip.

    `caption_style` overrides the template's caption look for this clip only.
    `caption_lines` replaces the caption text (see words_from_lines).
    """
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if not job:
            raise ValueError("Job not found.")
        clip = (session.query(Clip)
                .filter(Clip.job_id == job_id, Clip.index == index)
                .first())
        if not clip:
            raise ValueError("Clip not found.")
        if not job.transcript:
            raise ValueError(
                "This job has no stored transcript, so it cannot be edited. "
                "Jobs created before editing was added are affected -- re-run the video."
            )

        duration = end - start
        if duration < MIN_EDIT_SECONDS:
            raise ValueError(f"A clip must be at least {MIN_EDIT_SECONDS:.0f} seconds.")
        if duration > MAX_EDIT_SECONDS:
            raise ValueError(f"A clip cannot exceed {MAX_EDIT_SECONDS / 60:.0f} minutes.")
        if start < 0:
            raise ValueError("Start time cannot be negative.")

        options = job.options or {}
        tpl = templates.get(options.get("template", "classic"), templates["classic"])
        ratio = ratio or options.get("ratio", "9:16")
        job_dir = os.path.join(storage_dir, job_id)

        source = ensure_source(job, job_dir)

        out_w, out_h = render.RATIOS.get(ratio, render.RATIOS["9:16"])
        if caption_pos is not None:
            # An explicit position from the editor wins over the automatic
            # letterbox placement -- the user dragged it there on purpose.
            margin_v = render.caption_margin_from_position(out_h, caption_pos)
        elif tpl["frame"] == "gameplay":
            margin_v = render.split_caption_margin_v(out_h)
        else:
            src_w, src_h = render.probe_dimensions(source)
            margin_v = render.caption_margin_v(src_w, src_h, out_w, out_h)
        size_px = caption_size or None

        if caption_lines:
            words = words_from_lines(caption_lines, job.transcript)
        else:
            words = words_in_range(job.transcript, start, end)

        style = caption_style or (job.options or {}).get("caption_style_override",
                                                         tpl["caption_style"])

        caption_words, mute_spans = (censor.apply(words, start)
                                     if options.get("auto_censor", True) and words
                                     else (words, None))

        subtitle_path = None
        if options.get("burn_subtitles", True) and caption_words:
            subtitle_path = os.path.join(job_dir, f"clip_{index + 1}.ass")
            if translate_to:
                # Translated subtitles: whole lines on the original utterance
                # spans, since word-karaoke cannot survive translation. Audio
                # stays original -- the censor mute spans still apply.
                utt_lines = []
                for u in transcriber.get_utterances(job.transcript):
                    if u["end"] > start and u["start"] < end:
                        utt_lines.append({
                            "start": max(u["start"], start) - start,
                            "end": min(u["end"], end) - start,
                            "text": u["transcript"],
                        })
                if caption_lines:   # user edits win over the raw transcript
                    utt_lines = [{"start": l["start"] - start, "end": l["end"] - start,
                                  "text": l["text"]} for l in caption_lines]
                translated = translation.translate_lines(utt_lines, translate_to)
                subtitles.build_ass_lines(translated, subtitle_path, margin_v,
                                          style=style, play_res=(out_w, out_h),
                                          title=clip.title or "", font=caption_font,
                                          size_px=size_px, animation=caption_anim,
                                          title_style=title_style, title_font=title_font)
            else:
                subtitles.build_ass(caption_words, start, subtitle_path, margin_v,
                                    style=style, play_res=(out_w, out_h),
                                    title=clip.title or "", keywords=clip.keywords,
                                    font=caption_font, size_px=size_px, animation=caption_anim,
                                    title_style=title_style, title_font=title_font)

        out_name = f"clip_{index + 1}.mp4"
        final_path = os.path.join(job_dir, out_name)
        # Render to a SIDE file and swap it in atomically.
        #
        # Writing straight to clip_N.mp4 overwrites a file the browser may be
        # streaming right then -- the player keeps its byte offsets from the old
        # file and reads them out of the new one, which is why a re-exported clip
        # stuttered and froze in the page while the downloaded copy was perfect.
        # os.replace is atomic on the same filesystem: a reader sees either the
        # whole old file or the whole new one, never a half-written mix.
        tmp_path = os.path.join(job_dir, f".{out_name}.tmp.mp4")
        render.render_clip(
            source, start, end, tmp_path,
            subtitle_path=subtitle_path,
            gameplay_path=(gameplay_loops[index % len(gameplay_loops)]
                           if gameplay_loops else None),
            ratio=ratio,
            frame=tpl["frame"],
            overlay_list=overlay_list,
            mute_spans=mute_spans,
            speed=speed,
            speed_pitched=speed_pitched,
            background=background,
        )
        os.replace(tmp_path, final_path)

        clip.start = start
        clip.end = end
        # Duration is what the viewer experiences, so a speed change changes it.
        clip.duration = round(duration / max(speed or 1.0, 0.001), 1)
        clip.words = words
        session.commit()
        return clip.to_dict()
