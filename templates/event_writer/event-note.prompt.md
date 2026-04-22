# Write an event diary note

You are writing a first-person diary note about a single event. The event is grounded in file content that was literally read — raw extracted text and image captions are given below. Translate this evidence into a human, coherent account of what happened at the event. Do not copy, restate, or paraphrase chunks of the raw text.

## Event metadata

- **Date:** {event_date}
- **Project:** {project}
- **Domain:** {domain}
- **Subfolder:** {subfolder}
- **Source file:** {source_basename} ({file_type})

## Raw extracted text (evidence — never paste verbatim)

{raw_text_excerpt}

## Image captions (what was seen in the images — one line each)

{captions_block}

## Rules — fabrication firewall

1. **Ground every claim.** Every specific measurement, quote, decision, person, or date in your note must come from the raw text or a caption above. If you did not see it, do not write it.
2. **No verbatim paste.** Do not copy consecutive substrings (>60 chars) from the raw text into the note. Translate into your own words.
3. **First-person diary voice.** Write as the person who was at the event. Past tense. Conversational.
4. **Obsidian highlights and callouts where they help:**
   - `==highlight==` for a key fact literally seen (date, amount, dimension, named decision-maker) — use sparingly so highlights retain meaning.
   - `> [!important]` for a critical decision, deadline, or blocker present in the source.
   - `> [!warning]` for a caveat, risk, or issue in the source.
   - `> [!abstract] Overview\n> <one sentence>` optional — a one-sentence summary at the top is a nice hook but no longer required. When absent the MOC falls back to the first sentence of prose.
5. **Choose images.** The captions above are already the curated set (≤10). Do not add image references — the caller will append embeds.

## Shape is your choice (v15)

Pre-v15 this prompt required every note to start with `> [!abstract] Overview`, a 5-25 word abstract sentence, and a 100-200 word body paragraph. Those rules were format-as-validation: useful guidance inverted into a cage that forced retries on legitimately short field observations and legitimately long PDF analyses.

You now decide the shape based on the content:
- **A 2-line field observation** for a photo-only event is fine.
- **A tight 3-bullet list** for a structural-review memo is fine.
- **A 400-word prose analysis** for a long PDF is fine.
- **Plain prose** with no callouts is fine.
- **A `> [!important]` callout at the top** is fine when a single decision is the whole story.

Guidance remains: ~100-200 words for typical diary events, one sentence of opening orientation somewhere near the top (so the MOC's first-sentence fallback yields a useful hint), and never any fabricated specifics.

## Output

Return ONLY the body content — no frontmatter, no image embeds, no top-level `#` heading. The caller handles those.
