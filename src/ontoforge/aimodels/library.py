"""The LIVING prompt library (plan §3): many versions per task + an observation loop.

``prompts.py`` defines static, versioned templates. This module turns that static
registry into a *library* that holds **multiple versions per task**, tracks a
**champion** (the version served by default), and can **promote** the empirically
best version from an :class:`~ontoforge.aimodels.observation.ObservationLog` —
closing the loop between "what we asked" and "what worked".

Zero-regression seeding: a fresh :class:`PromptLibrary` SEEDS from the existing
``PROMPTS`` dict, so every current template is registered as its own version and
is the champion for its task. With no observations and no extra registrations the
library behaves exactly like ``get_prompt``.

Determinism: ``select_by_observations`` uses a fully specified tie-break (higher
mean_confidence, then higher count, then lexicographically smallest version), so a
crafted log promotes the same version on every run. Nothing here is time- or
random-dependent; keyless and offline.
"""

from __future__ import annotations

from typing import Optional

from .observation import ObservationLog
from .prompts import PROMPTS, PromptTemplate

__all__ = ["PromptLibrary"]


class PromptLibrary:
    """Multi-version, champion-tracking prompt store.

    Internals:
      * ``_versions``: ``{task: {version: PromptTemplate}}``
      * ``_champion``: ``{task: version}`` — the default version served by
        :meth:`get` when no explicit version is requested.

    Construction seeds from ``PROMPTS`` (every template becomes its task's sole
    version and champion) unless ``seed=False``.
    """

    def __init__(self, *, seed: bool = True) -> None:
        self._versions: dict[str, dict[str, PromptTemplate]] = {}
        self._champion: dict[str, str] = {}
        if seed:
            for template in PROMPTS.values():
                self.register(template)

    # ------------------------------------------------------------ registry

    def register(self, template: PromptTemplate) -> None:
        """Add or replace the ``(task, version)`` slot for ``template``.

        The champion is left unchanged, EXCEPT when this is the first version
        registered for a task — then it becomes the champion (so a freshly seeded
        or freshly populated task always has a usable default).
        """
        by_version = self._versions.setdefault(template.task, {})
        first = not by_version
        by_version[template.version] = template
        if first:
            self._champion[template.task] = template.version

    def tasks(self) -> tuple[str, ...]:
        """All known task names, sorted."""
        return tuple(sorted(self._versions))

    def versions(self, task: str) -> tuple[str, ...]:
        """The registered version strings for ``task``, sorted. Empty if unknown."""
        return tuple(sorted(self._versions.get(task, {})))

    def champion(self, task: str) -> str:
        """The current champion version string for ``task``."""
        if task not in self._champion:
            raise KeyError(f"no versions registered for task {task!r}")
        return self._champion[task]

    def get(self, task: str, version: Optional[str] = None) -> PromptTemplate:
        """The :class:`PromptTemplate` for ``task`` — the champion when ``version``
        is None, else the named version. Raises :class:`KeyError` if absent."""
        by_version = self._versions.get(task)
        if not by_version:
            raise KeyError(f"no versions registered for task {task!r}")
        if version is None:
            version = self._champion[task]
        if version not in by_version:
            raise KeyError(
                f"no version {version!r} for task {task!r}; have {sorted(by_version)}"
            )
        return by_version[version]

    def set_champion(self, task: str, version: str) -> None:
        """Promote ``version`` to champion for ``task`` (must already be registered)."""
        by_version = self._versions.get(task)
        if not by_version:
            raise KeyError(f"no versions registered for task {task!r}")
        if version not in by_version:
            raise KeyError(
                f"cannot set champion: no version {version!r} for task {task!r}; "
                f"have {sorted(by_version)}"
            )
        self._champion[task] = version

    # --------------------------------------------------------- observation loop

    def select_by_observations(self, obs_log: ObservationLog) -> None:
        """Promote each task's best-performing version per ``obs_log``.

        For each known task, summarize its observations and pick the winning
        version with a DETERMINISTIC tie-break:

          1. higher ``mean_confidence``,
          2. then higher ``count``,
          3. then lexicographically smallest ``version`` string.

        A task with no observations keeps its current champion. Versions that
        appear in the log but were never registered are ignored (the library only
        promotes among versions it actually holds).
        """
        for task, by_version in self._versions.items():
            summary = obs_log.summarize(task)
            if not summary:
                continue  # no evidence -> keep current champion
            candidates = [
                (
                    stats["mean_confidence"],
                    stats["count"],
                    version,
                )
                for version, stats in summary.items()
                if version in by_version
            ]
            if not candidates:
                continue
            # max by (mean_confidence, count) ascending; for the version tie-break we
            # want the LEXICOGRAPHICALLY SMALLEST, so sort and take the first by a
            # composite key that negates version order at the same (conf, count).
            best = min(
                candidates,
                key=lambda c: (-c[0], -c[1], c[2]),
            )
            self._champion[task] = best[2]
