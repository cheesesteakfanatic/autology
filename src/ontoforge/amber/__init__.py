"""M14 — AMBER: the freeze-frame snapshot (whitepaper §7, §11.2 M14).

NOTE: `ontoforge.amber.reader` is deliberately NOT imported here — it is the
bundle-side reference answerer and must stay importable without pulling in any
OntoForge module (the §7 loss-set negative test audits its imports).
"""

from .errors import AmberError
from .importer import import_bundle
from .ontology_json import ontology_from_obj, ontology_to_obj
from .snapshot import CAPABILITY_LOSS, FORMAT_VERSION, MANIFEST_NAME, SURVIVORSHIP_ORDER, snapshot
from .verify import verify

__all__ = [
    "AmberError",
    "snapshot",
    "verify",
    "import_bundle",
    "CAPABILITY_LOSS",
    "SURVIVORSHIP_ORDER",
    "MANIFEST_NAME",
    "FORMAT_VERSION",
    "ontology_to_obj",
    "ontology_from_obj",
]
