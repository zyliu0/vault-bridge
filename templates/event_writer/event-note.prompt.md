# Write an event diary note

You are writing a first-person diary note about a single event. The event is grounded in file content that was literally read — raw extracted text and image captions are given below. Your job is to **translate** this evidence into a human, coherent 100-200 word summary of what happened at the event. Do not copy, restate, or paraphrase chunks of the raw text; write what occurred.

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

## Required output structure

Begin with a one-sentence Obsidian abstract callout, then a blank line, then the 100-200 word body:

```
> [!abstract] Overview
> <one sentence, 5-25 words, summarising what happened at this event>

<100-200 word diary paragraph(s)>
```

The abstract sentence is the event's headline. The project-index MOC
reads it verbatim to give readers a one-line preview per event. Keep it
single-clause, specific, and grounded — same fabrication rules apply.

## Rules — fabrication firewall

1. **Ground every claim.** Every specific measurement, quote, decision, person, or date in your note must come from the raw text or a caption above. If you did not see it, do not write it.
2. **No verbatim paste.** Do not copy consecutive substrings (>60 chars) from the raw text into the note. Translate.
3. **First-person diary voice.** Write as the person who was at the event. Past tense. Conversational.
4. **Length: 100-200 words** for the body paragraph(s). The abstract callout does NOT count toward this range.
5. **Abstract callout is required.** Every event note starts with `> [!abstract] Overview\n> <one sentence>`. Without it the project-index builder cannot surface the event and the validator rejects the note.
6. **Other Obsidian highlights and callouts where they help:**
   - `==highlight==` for a key fact literally seen (date, amount, dimension, named decision-maker).
   - `> [!important]` for a critical decision, deadline, or blocker in the source.
   - `> [!warning]` for a caveat, risk, or issue in the source.
   - Use sparingly — most notes need zero callouts beyond the abstract.
7. **Never use these forbidden phrases** (they are signals of fabrication): "the team said", "the review came back", "pulled the back wall in", "half a storey", "[person] said", "40cm" (or any measurement not literally in the source).
8. **Choose images.** The captions above are already the curated set (≤10). Do not add image references — the caller will append embeds.

## Output

Return ONLY the body content (abstract callout + blank line + prose). No frontmatter. No image embeds. No `#` heading.
