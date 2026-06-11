"""Estate packages: standing test corpora + gold artifacts (whitepaper §17).

v0 ships the aviation hero estate (§17.2.1) at pinned fixture scale (AMD-0006).
"""

from .aviation import (
    load_competency_questions,
    load_er_gold_pairs,
    load_estate,
    load_gold_ontology,
)
from .gold import load_gold_ontology as load_gold_ontology_file

__all__ = [
    "load_estate",
    "load_gold_ontology",
    "load_gold_ontology_file",
    "load_competency_questions",
    "load_er_gold_pairs",
]
