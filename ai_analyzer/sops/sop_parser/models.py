"""The SOP section: what SOPParser produces and every retriever searches over."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SOPSection:
    """One "## <ID>: <title>" section of an SOP file: the unit we retrieve."""

    sop_id: str                 # e.g. "PAY-02"
    title: str                  # e.g. "Deposit failed but money debited from bank"
    category: str               # from the file header, e.g. "payments"
    source_file: str            # e.g. "payment_sop.txt"
    text: str                   # the full section, heading included
    customer_may_say: list[str] = field(default_factory=list)  # the phrasings listed in the section
