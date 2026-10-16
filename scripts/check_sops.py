"""Consistency checks for the SOP knowledge base.

    python scripts/check_sops.py    (checks ai_analyzer/sops)

Checks, in order:
    1. every SOP file is plain ASCII
    2. section ids are unique across files
    3. cross references ("payment_sop PAY-02", "see PAY-02", "kyc_sop") resolve
    4. SOPs call tools, never SQL, and only with tools and arguments the LLM is offered
    5. no internal codes or table names leak into customer reply templates
"""

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

SOPS = Path(sys.argv[1] if len(sys.argv) > 1 else "ai_analyzer/sops")

# tools.json has no user_id: ToolSvc injects it, so the LLM never passes one.
TOOLS_JSON = Path(__file__).resolve().parents[1] / "ai_analyzer" / "tools" / "tools.json"
TOOL_PARAMS = {
    tool["name"]: set(tool["parameters"]["properties"]) for tool in json.loads(TOOLS_JSON.read_text())
}
TOOLS = set(TOOL_PARAMS)

SECTION_ID = r"[A-Z]{2,4}-\d{2}"
TOOL_CALL = re.compile(r"\b((?:get|check|find)_[a-z0-9_]+)\(([^()]*)\)")


def main() -> None:
    sop_texts = {path.stem: path.read_text() for path in sorted(SOPS.glob("*.txt"))}
    check_ascii(sop_texts)
    files_of_id = check_unique_ids(sop_texts)
    check_references(sop_texts, files_of_id)
    check_tool_calls(sop_texts)
    check_reply_template_leaks(sop_texts)


# ================================================================ 1. ASCII

def check_ascii(sop_texts: dict[str, str]) -> None:
    for stem, text in sop_texts.items():
        bad_line_numbers = [number for number, line in enumerate(text.splitlines(), start=1)
                            if not line.isascii()]
        if bad_line_numbers:
            print(f"NON-ASCII {stem}: {len(bad_line_numbers)} lines, e.g. line {bad_line_numbers[0]}")


# ================================================================ 2. unique section ids

def check_unique_ids(sop_texts: dict[str, str]) -> dict[str, list[str]]:
    """Print duplicate ids; return section id -> the files that define it."""
    files_of_id = defaultdict(list)
    for stem, text in sop_texts.items():
        for match in re.finditer(rf"^## ({SECTION_ID}):", text, re.M):
            files_of_id[match.group(1)].append(stem)

    duplicates = {sop_id: stems for sop_id, stems in files_of_id.items() if len(stems) > 1}
    print(f"sections: {len(files_of_id)} unique ids; duplicates: {duplicates or 'none'}")
    return files_of_id


# ================================================================ 3. cross references

def check_references(sop_texts: dict[str, str], files_of_id: dict[str, list[str]]) -> None:
    known_prefixes = {sop_id.split("-")[0] for sop_id in files_of_id}
    broken = []
    for stem, text in sop_texts.items():
        broken += broken_file_and_id_references(stem, text, sop_texts, files_of_id)
        broken += broken_bare_id_references(stem, text, known_prefixes, files_of_id)
        broken += broken_file_references(stem, text, sop_texts)

    print(f"broken references: {len(broken)}")
    for reference in sorted(set(broken)):
        print("  ", reference)


def broken_file_and_id_references(stem, text, sop_texts, files_of_id) -> list[tuple]:
    """'<file_stem> <ID>': the file must exist and be the only one defining the id."""
    broken = []
    for match in re.finditer(rf"\b([a-z_]+_sop|data_dictionary)\s+({SECTION_ID})\b", text):
        target, sop_id = match.groups()
        if target not in sop_texts:
            broken.append((stem, match.group(0), "no such file"))
        elif files_of_id.get(sop_id) != [target]:
            broken.append((stem, match.group(0), f"id lives in {files_of_id.get(sop_id)}"))
    return broken


def broken_bare_id_references(stem, text, known_prefixes, files_of_id) -> list[tuple]:
    """Bare ids like "see PAY-02" must exist somewhere."""
    broken = []
    for match in re.finditer(rf"\b({SECTION_ID})\b", text):
        sop_id = match.group(1)
        if sop_id.split("-")[0] in known_prefixes and sop_id not in files_of_id:
            broken.append((stem, sop_id, "id does not exist"))
    return broken


def broken_file_references(stem, text, sop_texts) -> list[tuple]:
    broken = []
    for match in re.finditer(r"\b([a-z_]+_sop)\b", text):
        if match.group(1) not in sop_texts:
            broken.append((stem, match.group(1), "no such file"))
    return broken


# ================================================================ 4. tool calls, no SQL

def check_tool_calls(sop_texts: dict[str, str]) -> None:
    """Every tool call names a tool and arguments the LLM is offered (tools.json), and each
    section's "TOOLS:" line lists exactly the tools the section calls."""
    sql_left, tables_lines, bad_calls, tools_line_mismatch = [], [], [], []
    tool_use = Counter()
    for stem, text in sop_texts.items():
        if stem == "data_dictionary":
            continue
        for section, body in split_sections(text):
            if re.search(r"\bSELECT\b|\bFROM\s+[a-z_]+\s+WHERE\b", body):
                sql_left.append((stem, section))
            if re.search(r"^TABLES:", body, re.M):
                tables_lines.append((stem, section))

            called = []
            for name, args in TOOL_CALL.findall(body):
                if name not in TOOLS:
                    bad_calls.append((stem, section, f"unknown tool {name}"))
                    continue
                called.append(name)
                tool_use[name] += 1
                for arg in split_arguments(args):
                    arg_name = arg.split("=", 1)[0].strip()
                    if arg_name not in TOOL_PARAMS[name]:
                        bad_calls.append((stem, section, f"{name}: unknown argument {arg_name!r}"))

            listed_tools = tools_line(body) - {"none"}
            unknown_listed = listed_tools - TOOLS
            if unknown_listed:
                bad_calls.append((stem, section, f"TOOLS line names unknown {sorted(unknown_listed)}"))
            if called and listed_tools != set(called):
                tools_line_mismatch.append((stem, section, sorted(set(called) ^ listed_tools)))

    print(f"sections still containing SQL: {len(sql_left)} {sql_left[:10]}")
    print(f"sections still with a TABLES: line: {len(tables_lines)}")
    print(f"bad tool calls: {len(bad_calls)}")
    for bad_call in bad_calls:
        print("  ", bad_call)
    print(f"TOOLS line differs from the tools the section calls: {len(tools_line_mismatch)}")
    for mismatch in tools_line_mismatch:
        print("  ", mismatch)
    never_used = sorted(TOOLS - set(tool_use)) or "none"
    print(f"tool calls: {sum(tool_use.values())}; tools never used by any SOP: {never_used}")


def split_sections(text: str) -> list[tuple[str, str]]:
    """(section id, body) pairs; the text before the first section comes first as "(header)"."""
    parts = re.split(rf"^(## {SECTION_ID}:[^\n]*)\n", text, flags=re.M)
    headings_and_bodies = [("(header)", parts[0])] + list(zip(parts[1::2], parts[2::2]))
    return [(heading.split(":")[0].removeprefix("## "), body) for heading, body in headings_and_bodies]


def tools_line(body: str) -> set[str]:
    """The tools named on the section's "TOOLS:" line (empty if it has none)."""
    listed = re.search(r"^TOOLS:(.*)$", body, re.M)
    if not listed:
        return set()
    return {tool.strip() for tool in listed.group(1).split(",") if tool.strip()}


def split_arguments(args: str) -> list[str]:
    """Split 'a=1, b=["X", "Y"]' on top-level commas only, so list values stay whole."""
    parts, depth, current = [], 0, ""
    for ch in args:
        depth += (ch == "[") - (ch == "]")
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    return [part.strip() for part in parts + [current] if part.strip()]


# ================================================================ 5. leaks into reply templates

# The template runs until the next "LABEL:" line, a "=====" rule or the end of the file.
REPLY_TEMPLATE = re.compile(
    r"CUSTOMER REPLY TEMPLATE[^\n]*\n(.*?)(?=\n[A-Z][A-Z /]+:\s*\n|\n=====|\Z)", re.S
)
INTERNAL_CODE = re.compile(r"\b[A-Z]{2,}(?:_[A-Z0-9]+)+\b")  # e.g. KYC_PENDING


def check_reply_template_leaks(sop_texts: dict[str, str]) -> None:
    tables = set(re.findall(r"^TABLE (\w+)", sop_texts["data_dictionary"], re.M))
    leaks = []
    for stem, text in sop_texts.items():
        for template in REPLY_TEMPLATE.findall(text):
            for code in INTERNAL_CODE.findall(template):
                leaks.append((stem, code))
            for table in tables:
                if re.search(rf"\b{table}\b", template):
                    leaks.append((stem, f"table:{table}"))

    print(f"possible leaks in reply templates: {len(leaks)}")
    for leak in sorted(set(leaks)):
        print("  ", leak)


if __name__ == "__main__":
    main()
