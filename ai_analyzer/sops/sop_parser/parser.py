"""Reads the SOP .txt files and splits each file into sections.

This is the only code that opens SOP files. Everything else asks SOPParser.

A section looks like this in the file:

    ======================================================================
    ## PAY-02: Deposit failed but money debited from bank
    ======================================================================
    CUSTOMER MAY SAY:
      - "Money deducted but not added to wallet"
    ...
"""

import re
from pathlib import Path

from ai_analyzer.sops.sop_parser.models import SOPSection

SOPS_DIR = Path(__file__).parent.parent

# Never searched: the guidelines always go into the prompt, and the
# data dictionary is for tools (see sops/README.md).
SKIPPED_FILES = {"support_guidelines_sop.txt", "data_dictionary.txt"}

# "## PAY-02: Deposit failed but money debited" -> ("PAY-02", "Deposit failed but money debited")
HEADING = re.compile(r"## ([A-Z]+-\d+):(.+)")


class SOPParser:
    """Parses every searchable SOP file into sections, once, and caches them."""

    def __init__(self, sops_dir=SOPS_DIR, skipped_files=SKIPPED_FILES):
        self.sops_dir = Path(sops_dir)
        self.skipped_files = skipped_files
        self.sections = None  # filled on the first call to parse()

    def parse(self) -> list[SOPSection]:
        """All searchable sections. The files are read only once."""
        if self.sections is None:
            self.sections = []
            for path in sorted(self.sops_dir.glob("*.txt")):
                print("\npath is ", path)
                if path.name not in self.skipped_files:
                    self.sections.extend(self.parse_file(path))
        return self.sections

    def parse_file(self, path: Path) -> list[SOPSection]:
        """The sections of one SOP file, in file order."""
        lines = path.read_text().splitlines()
        category = find_category(lines)

        sections = []
        section_lines = None  # lines of the section we are reading now
        for line in lines:
            if line.startswith("## "):  # a new section starts
                if section_lines:
                    sections.append(make_section(section_lines, category, path.name))
                section_lines = [line]
            elif section_lines is not None and not line.startswith("====="):
                section_lines.append(line)

        if section_lines:  # the last section in the file
            sections.append(make_section(section_lines, category, path.name))
        return sections


def find_category(lines: list[str]) -> str:
    """The value of the "CATEGORY: payments" line in the file header."""
    for line in lines:
        if line.startswith("CATEGORY:"):
            return line.split(":", 1)[1].strip()
    return "unknown"


def make_section(lines: list[str], category: str, file_name: str) -> SOPSection:
    heading = HEADING.match(lines[0])
    if heading is None:
        raise ValueError(f"{file_name}: bad section heading {lines[0]!r}")

    return SOPSection(
        sop_id=heading.group(1),
        title=heading.group(2).strip(),
        category=category,
        source_file=file_name,
        text="\n".join(lines).strip(),
        customer_may_say=find_customer_phrases(lines),
    )


def find_customer_phrases(lines: list[str]) -> list[str]:
    """The "- ..." lines under "CUSTOMER MAY SAY:", without the dash and quotes."""
    phrases = []
    in_list = False
    for line in lines:
        line = line.strip()
        if line.startswith("CUSTOMER MAY SAY:"):
            in_list = True
        elif in_list and line.startswith("-"):
            phrases.append(line.lstrip("- ").strip('"'))
        elif in_list and line.isupper() and line.endswith(":"):
            break  # the next label, e.g. "INFO NEEDED FROM CUSTOMER:"
    return phrases
