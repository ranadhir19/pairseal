"""pairseal — dual-read agreement-gated field extraction from
document images (invoices, bank checks, flyers, receipts, generic
forms). Only fields and items two independent vision-model reads
agree on are sealed; everything else is a reason-coded refusal.
"""

__version__ = "0.1.0"

from .gate import extract  # noqa: E402
from .providers import (DEFAULT_PAIR, GeminiReader, GroqReader,
                        OCRReader, make_reader)  # noqa: E402

__all__ = [
    "extract",
    "make_reader",
    "GeminiReader",
    "GroqReader",
    "OCRReader",
    "DEFAULT_PAIR",
    "__version__",
]
