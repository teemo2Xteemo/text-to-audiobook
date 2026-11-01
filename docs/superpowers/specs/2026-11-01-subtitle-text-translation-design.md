# Subtitle & text translation (no audio) — design

Date: 2026-11-01 · Status: draft for approval

## Intent

Not every user wants audio. A common need: translate a subtitle file (or plain text) and import
the result straight into **CapCut** as video subtitles. Today the app can only translate *on the
way to* audio, and its subtitle handling is built for narration (lossy), so its `.srt` output does
not line up cue-for-cue with the source video.

Success: a user drops `video01.srt` (or `.vtt` / `.ass` / `.txt`, or pastes text), picks a target
language, waits for the job, downloads `video01.vi.srt`, imports it into CapCut, and every cue
appears at the same time as the original.

## Goals

- New UI mode/tab **"Chỉ dịch"** (translate-only), separate from Translate and Narrate (user chose
  layout A). It hides voice, speed, audio format and every TTS control.
- Inputs: upload `.srt`, `.vtt`, `.ass`, `.txt`, or text pasted into the textbox.
- Output: a downloadable translated file.
  - Subtitle input (`.srt` / `.vtt` / `.ass`) → **same format as input**, format selector locked.
  - Text input (pasted text or `.txt`) → user picks `.txt` / `.srt` / `.vtt` / `.ass`,
    **default `.txt`** (user's decision for pasted text; `.txt` uploads get the same choice — our call).
- Runs as an async job on the existing pipeline: chunking, retry, cache, checkpoint, resume. A
  failed long file resumes without re-translating finished chunks.

## Non-goals

- No TTS, no audio, no audio-aligned timeline.
- No subtitle editor/preview, no timing correction, no bilingual (dual-language) output.
- No new formats beyond the four listed.
- Not changing how Translate/Narrate modes parse subtitles today.

## Anti-goals (fails even if it "works")

- Output whose cue count or timecodes differ from the source subtitle file.
- Reusing the narration parser (`parse_srt.py`, `parse.py:strip_srt`) for this mode: it drops
  sound-effect cues, strips tags, merges rolling captions and packs cues into chunks — all wrong
  for subtitle output.
- Hard-coding any language or language pair; calling a vendor API directly instead of
  `TranslationProvider`.

## Input type detection (our call)

Format is detected **by content, not by extension**, for uploads and pasted text alike: SRT, VTT
or ASS content is treated as that subtitle format (format selector locked to it) even when it
arrives as pasted text or a `.txt` file, or the extension does not match (e.g. a `.srt` holding VTT). Only content
that is none of the three is "text input". Reason: users often paste subtitles copied from a
player; sending timecodes to the translator would defeat the CapCut goal. The backend already
detects SRT/VTT by content (`is_srt_document`); ASS detection (`[Script Info]` / `[Events]` +
`Dialogue:`) is new. The frontend may sniff the same way to lock the selector; the backend is the
source of truth.

## Behaviour — subtitle files (`.srt` / `.vtt` / `.ass`)

- **Preserve exactly:** cue count, cue order, cue indices, start/end timecodes.
- **Translation unit = sentence, not cue (user's decision).** Consecutive cues that form one
  sentence are joined, translated together for correct meaning, then the translation is split back
  across the original cues. Slight text/speech misalignment is acceptable.
  - Split rule (our call): distribute by each cue's share of source text length; snap split points
    to punctuation or whitespace; for scripts without word spaces, split by character.
  - Every original cue must receive non-empty text if it had text in the source.
- **Batching (user's decision, option b):** several sentences go to the provider in one call,
  joined with an explicit separator. The response is split on the separator and the sentence
  count is checked. On a match, each sentence's translation goes only to that sentence's cues. On a
  mismatch, that chunk falls back to one provider call per sentence. A sentence's text must never
  drift onto another sentence's cues. The fallback reuses the existing per-chunk retry/cache;
  per-sentence results are cached so resume does not repeat them.
- **Line breaks inside a cue (our call):** each output cue is a single line; source line breaks
  inside a cue (`\n` in SRT/VTT, `\N` in ASS) are not reconstructed. CapCut wraps text to its box.
- **Sound-effect-only cues** (`[Music]`, `♪ … ♪`, etc.) are kept unchanged, not translated, not
  deleted (our call).
- **Formatting tags** (`<i>`, `{\an8}` …): removed before translation. A tag at the very start of
  a cue (e.g. position override `{\an8}`) is re-attached to that cue; inline mid-sentence tags may be
  lost (our call, accepted).
- **`.ass`:** keep `[Script Info]`, `[V4+ Styles]`, `[Events]` header and every non-Text field of
  `Dialogue:` lines (Layer, Start, End, Style, Name, margins, Effect) text-identical (uploads are
decoded to text with BOM removed and newlines normalised, so byte-identical is not possible). Translate only
  the Text field. `Comment:` lines untouched. `\N` line breaks inside Text count as whitespace for
  translation.
- **`.vtt`:** keep the `WEBVTT` header, cue identifiers and cue settings (`align:` etc.); `NOTE` /
  `STYLE` blocks untouched.
- Output encoding: UTF-8.

## Behaviour — text input (pasted text or `.txt`)

- **Output `.txt`:** keep line and paragraph structure; each paragraph translated as a paragraph.
- **Output `.srt` / `.vtt` / `.ass`** (no source timing, our call):
  - One cue per non-empty source line; a line longer than ~2 subtitle lines is split further at
    punctuation.
  - Duration estimated from text length, clamped to about 1–7 s; cues back-to-back from
    `00:00:00`. Timing is a rough placeholder — users re-time in CapCut.
  - New `.ass` files use one simple default style (standard font, white text, black outline,
    bottom-centre).
  - Cue splitting happens on the **translated** text (builder may decide source vs translated;
    translated is preferred so cue lengths fit the target language).

## Download

- After completion the job view shows "Đã dịch N / N dòng" and a download button.
- Filename: `<original-basename>.<target_language>.<ext>`; pasted text uses a generic basename
  (e.g. `translation`). Original filename is untrusted: sanitise before reuse in
  `Content-Disposition`.

## Constraints

- Follow `AGENTS.md`, `.cursor/rules/`, `docs/ai/`: language-agnostic, provider ports only,
  async jobs, per-chunk retry/cache/checkpoint/resume, untrusted uploads/filenames.
- Upload limits and encoding detection already in `backend/app/api/parse.py`
  (`max_upload_bytes`, `_decode_source_text`). `.ass` must be added to the accepted extensions/MIME
  list; the frontend currently only accepts `.txt/.srt/.vtt` (`JobForm.tsx`).
- Existing job model: `JobMode` (`translate`, `narrate`), `OutputFormat` (`mp3`, `wav`),
  pipeline statuses include TTS stages (`backend/app/domain/jobs.py`).

## What already exists to reuse

- Translation chunk loop with retry/cache/checkpoint: `PipelineOrchestrator._translate_chunks`
  (`backend/app/application/pipeline/orchestrator.py`), `translate_chunk`
  (`application/pipeline/translate.py`).
- Timecode parse/format helpers: `parse_timecode_line`, `format_srt_timecode`
  (`application/pipeline/parse_srt.py`) — reusable as helpers, not the cue parser itself.
- Sound-cue detection: `is_sound_effect_line`.
- Subtitle download endpoint pattern: `GET /api/jobs/{id}/subtitles` (`api/jobs.py`), output path
  helper `JobService.output_subtitles_path`.
- Language detection: `resolve_source_language`.

## Left to the builder

- Data model: new `JobMode` value vs other representation; how the requested output format and
  original filename/format are stored on the job (must survive resume); which pipeline statuses the
  mode walks (it must not claim TTS stages in the UI).
- Endpoint shape: extend `/subtitles` or add a generic translated-output endpoint; MIME types per
  format.
- Sentence grouping heuristic across cues and the exact split algorithm, within the rules above.
- Module layout for the new lossless parsers/writers (likely one per format in
  `application/pipeline/`).
- Chunk sizing (group sentences into chunks within the provider's `max_chars`) and the separator
  token used for batching (must survive NLLB and LLM providers; tested per provider fake).
- Tests: round-trip (parse → write without translation reproduces timing and structure) per
  format, cue-count invariant, `.ass` header/field preservation, split-back on multi-cue sentences,
  resume without re-translation, frontend tab and download.
