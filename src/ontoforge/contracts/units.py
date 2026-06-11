"""Physical dimension vectors (whitepaper §3.2).

A dimension is a vector of integer exponents over the SI basis plus currency and
count pseudo-dimensions. Two properties may only be merged/compared/summed when
their dimension vectors match; LODESTONE's type checker rejects unit mixing
statically (§6.2 well-formedness).

Basis order: (m, kg, s, A, K, mol, cd, currency, count)
"""

from __future__ import annotations

from dataclasses import dataclass

BASIS = ("m", "kg", "s", "A", "K", "mol", "cd", "currency", "count")
_N = len(BASIS)


@dataclass(frozen=True, slots=True)
class Dimension:
    exps: tuple[int, ...] = (0,) * _N

    def __post_init__(self) -> None:
        if len(self.exps) != _N:
            raise ValueError(f"dimension vector must have {_N} components")

    def __mul__(self, other: "Dimension") -> "Dimension":
        return Dimension(tuple(a + b for a, b in zip(self.exps, other.exps)))

    def __truediv__(self, other: "Dimension") -> "Dimension":
        return Dimension(tuple(a - b for a, b in zip(self.exps, other.exps)))

    @property
    def dimensionless(self) -> bool:
        return all(e == 0 for e in self.exps)

    def __str__(self) -> str:
        parts = [f"{b}^{e}" if e != 1 else b for b, e in zip(BASIS, self.exps) if e != 0]
        return "·".join(parts) if parts else "1"


def dim(**kw: int) -> Dimension:
    """dim(m=1, s=-1) -> velocity dimension."""
    exps = [0] * _N
    for k, v in kw.items():
        exps[BASIS.index(k)] = v
    return Dimension(tuple(exps))


DIMENSIONLESS = Dimension()
LENGTH = dim(m=1)
MASS = dim(kg=1)
TIME = dim(s=1)
TEMPERATURE = dim(K=1)
CURRENCY = dim(currency=1)
COUNT = dim(count=1)
SPEED = dim(m=1, s=-1)


@dataclass(frozen=True, slots=True)
class UnitDef:
    """A concrete unit: symbol, dimension, affine conversion to the canonical unit.

    canonical_value = scale * value + offset   (offset only for temperatures)
    """

    symbol: str
    dimension: Dimension
    scale: float = 1.0
    offset: float = 0.0
    canonical: str = ""   # symbol of the canonical unit for this dimension

    def to_canonical(self, v: float) -> float:
        return self.scale * v + self.offset

    def from_canonical(self, v: float) -> float:
        return (v - self.offset) / self.scale
