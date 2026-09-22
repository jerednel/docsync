# Writing rules for documentation pages

Definition of done for every page you touch: **Relevant, Sensible, Effective, Complete.**
A PM, an analyst and a data engineer must each find their answer in under a minute.

## Structure
- **Answer first.** The first sentence of any section states the fact. Reasons and caveats follow.
- **Same headings every time.** Every pipeline section, every table section uses the template's headings in the same order. Readers learn the shape once.
- **One home per fact.** A fact lives on exactly one page (see page-model.md). Everywhere else, link to it. Never restate a rule on a second page.
- **Tables for parallel facts, sentences for reasoning.** Owners, schedules, paths, columns go in tables. Why a rule exists goes in one or two sentences.
- **Short blocks.** Sections under ~150 words. Lists of at most 7 items; split or table beyond that.
- **A `[TOC]` at the top of any page over two screens.**

## Sentences
- Plain technical English. Under 25 words. One idea each. Active voice: "The provider lands the file", not "the file is landed".
- Concrete strings in code font: `s3://bucket/raw/sales/`, `fct_sales`, `order_id`. Never paraphrase a path or table name.
- Define a term once, in the Glossary. Link the first use on each page.
- Write the three-reader sentence for every entity: what it is (PM), what one row / one file is (analyst), where it is in the repo (engineer).
- Numbers, dates, timezones explicit: "Mondays 06:00 UTC", not "weekly in the morning".

## Keeping pages true
- **Replace, do not append.** When something changes, rewrite the sentence or row. Stale text is deleted, not struck through.
- **No placeholders, no TODO, no "TBC"** in a published page. If a fact is unknown, write "Unknown — owner: <name> to confirm" and add an audit entry so it is tracked.
- **Assumptions are numbered and never renumbered.** Retire with Status = Retired and a Since date.
- **Mark change inside content only where readers need it:** a `Since` column, a "Changed 2026-09-18:" lead-in on a rule. The page banner already shows the last sync commit and PR.
- **Diagrams must be reproducible.** Mermaid in a code block, or a table. Never a pasted screenshot.

## Tone
- Neutral, direct, no marketing language, no hedging.
- Write for someone who is tired and interrupted: they should be able to stop at any heading and have learned one complete thing.
