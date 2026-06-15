"""The NL-DE (natural-language data-engineering) layer.

A deterministic, keyless parse+apply layer over TEMPER / ANVIL / ER: plain-English
data-engineering imperatives ("link orders to customers on customer_id", "treat
amount as currency", "rename qty to quantity") are parsed by cue-word + slot
matching against the LIVE ontology/estate (NO LLM, NO embeddings, NO network),
compiled to an existing operator, run through a mandatory PREVIEW (a real dry-run
that computes coverage/impact on actual cells), then a CONFIRM step that applies
via the real engine machinery and returns an exact one-click undo token.

The confidently-wrong guard is structural at every layer:

* the PARSER only proposes — an ambiguous/unmatched command returns a single
  clarification question, never a guessed operator (LODESTONE's clarify-don't-
  guess contract);
* the OPERATOR's own precondition + the preview's coverage threshold (for links)
  + the spine gate (for Merge/Split) decide whether it can apply — nothing is
  asserted as fact from a sentence.
"""

from .commands import (
    PARSEABLE_KINDS,
    ClarificationNeeded,
    ParseResult,
    ProposedCommand,
    UnsupportedCommand,
    parse_command,
)
from .operators import EngineerService, OperatorPreview

__all__ = [
    "PARSEABLE_KINDS",
    "ClarificationNeeded",
    "EngineerService",
    "OperatorPreview",
    "ParseResult",
    "ProposedCommand",
    "UnsupportedCommand",
    "parse_command",
]
