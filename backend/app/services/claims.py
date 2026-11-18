"""Evidence check: is each fact in an answer key actually in the course material?

The six compliance checks say a draft has the right shape, level and length, and that the
question cites the material. None of them reads the *answer key* for facts the material never
stated. Measured on real answer keys, embedding similarity cannot do it either: a fabricated
on-topic sentence ("a use case diagram must contain exactly seven actors") scored 0.56 against
the passages, while a correct sentence applying the material to the question's own scenario
scored 0.21.

So the check is split in two, and only the half a program can do is trusted:

  * a model lists the factual statements in the answer key and, for each one it says comes from
    the material, gives the passage number and the exact sentence that states it;
  * this module then looks for that sentence in that passage, verbatim. A statement whose quote
    is not there is reported as unsupported, whatever the model said about it.

What this does and does not establish: a statement marked supported has a real sentence of the
material next to it for the reviewer to read. Whether that sentence truly entails the statement
is still a judgement, and statements about the question's own invented scenario cannot be
checked against the material at all — they are listed as such. It is evidence for a reviewer,
never a verdict, and it gates nothing.
"""

import re

from ..llm import GenerationRequest

SYSTEM = """You check an examination answer key against the course passages it was written from.
List every factual statement the answer key makes. For each one decide:
  - "material": it asserts a rule, definition, property, purpose or classification of the subject.
    A statement is "material" whenever it would still be a claim about the subject if the case
    were removed, even when it is worded about the case ("this link is wrong because an external
    entity may only connect to a process" asserts a rule, so it is "material");
  - "scenario": it only names or arranges things that belong to the particular case the question
    describes (its actors, data, figures, steps) and asserts nothing general.
When a sentence does both, split it and list the general part as "material".
For a "material" statement give the number of the passage that states it and copy the single
sentence from that passage that states it, exactly, character for character. If no passage
states it, give passage 0 and an empty quote. Do not rewrite, shorten or merge quotes."""

SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "kind": {"type": "string", "enum": ["material", "scenario"]},
                    "passage": {"type": "integer"},
                    "quote": {"type": "string"},
                },
                "required": ["text", "kind", "passage", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["claims"],
    "additionalProperties": False,
}

MAX_CLAIMS = 20
MIN_QUOTE_WORDS = 4  # a two-word "quote" is found in any passage and proves nothing


def _flat(text: str) -> str:
    """The words of a text, in order, and nothing else.

    Lecture slides reach here with bullets, line breaks and typographic hyphens that a model
    copying a sentence does not reproduce, so comparing raw characters reported real sentences
    as missing. Comparing the sequence of words keeps what matters — the same words in the same
    order, contiguously — and ignores how they were punctuated."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def verify(raw: object, passages: list) -> dict | None:
    """Turn the model's list into a verified report, or None when it is not usable.

    Nothing the model says about support is taken on trust: `verified` is set here, by finding
    the quote in the passage."""
    items = raw.get("claims") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return None
    flat = [_flat(p.text) for p in passages]
    claims: list[dict] = []
    for item in items[:MAX_CLAIMS]:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
            continue
        kind = "scenario" if item.get("kind") == "scenario" else "material"
        quote = item.get("quote") if isinstance(item.get("quote"), str) else ""
        verified, page_ref = False, None
        needle = _flat(quote)
        if kind == "material" and len(needle.split()) >= MIN_QUOTE_WORDS:
            try:
                first = int(item.get("passage")) - 1
            except (TypeError, ValueError):
                first = -1
            # The passage it named first, then the others: a right quote with a wrong number
            # is still a right quote.
            order = ([first] if 0 <= first < len(passages) else []) + [i for i in range(len(passages)) if i != first]
            for i in order:
                if f" {needle} " in f" {flat[i]} ":  # whole words only
                    verified, page_ref = True, passages[i].page_ref
                    break
        claims.append({
            "text": item["text"].strip(), "kind": kind, "verified": verified,
            "quote": quote.strip() if verified else None, "page_ref": page_ref,
        })
    if not claims:
        return None
    material = [c for c in claims if c["kind"] == "material"]
    return {
        "claims": claims,
        "material_total": len(material),
        "material_verified": sum(1 for c in material if c["verified"]),
        "scenario_total": len(claims) - len(material),
    }


def check(adapter, stem: str, answer_key: str, passages: list) -> dict | None:
    """One evidence report for one finished draft. Never raises: a check that cannot be made
    is reported as absent, and must not cost the run the question it was about."""
    if not passages:
        return None
    user = (
        f"Question:\n{stem}\n\nAnswer key:\n{answer_key}\n\nCourse passages:\n"
        + "\n\n".join(f"[{i}] {p.text}" for i, p in enumerate(passages, start=1))
    )
    try:
        result = adapter.generate(GenerationRequest(
            system=SYSTEM, user=user, json_schema=SCHEMA, temperature=0.0, seed=None,
        ))
        report = verify(result.data, passages)
    except Exception:
        return None
    if report is not None:
        report["checked_by"] = getattr(adapter, "backend_id", "unknown")
    return report
