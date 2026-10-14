# Pure metadata helpers adapted from Report AI, read-only source review 2026-10-12.
# No Report AI application, database or worker is imported.
from __future__ import annotations
import re
from pathlib import Path

ABSTRACT_RE = re.compile(
    r"\bAb\s*stract\s*[-—:]?\s*(?P<body>.*?)"
    r"(?=\s+(?:(?:I|1|Ⅰ)\s*\.\s*(?:\d+B)?\s*)?I\s*NTRODUCTION\b"
    r"|\s+INDEX\s+TERMS?\b|\Z)",
    re.IGNORECASE | re.DOTALL,
)

FIRST_SECTION_RE = re.compile(
    r"(?:^|\s)(?:(?:I|1|Ⅰ)\s*\.\s*(?:\d+B)?\s*)?I\s*NTRODUCTION\b",
    re.IGNORECASE,
)

AFFILIATION_MARKERS = (
    "university",
    "institute",
    "corporation",
    "company",
    "laboratory",
    "department",
    "school of",
    "email",
    "e-mail",
)

BAD_METADATA_TITLES = (
    "untitled",
    "microsoft word",
    "powerpoint presentation",
    "adobe acrobat",
)

WORKING_METADATA_TITLE_RE = re.compile(
    r"(?i)^20\d{6}_|^IEDM[_\s-]|"
    r"(?:^|[_\s-])(?:draft|final|v\d+)(?:$|[_\s-])|"
    r"\.(?:pdf|pptx?)$"
)

TITLE_BOILERPLATE_PREFIXES = (
    "distribution statement",
    "snapdragon and qualcomm branded products",
    "tsmc property",
)

TITLE_SESSION_CODE_RE = re.compile(
    r"^(?:<\s*)?"
    r"(?:JFS|TFS|PL|C|T)\d{1,3}(?:[-._]\d{1,3})+"
    r"(?:\s*>)?"
)

TITLE_SESSION_PREFIX_RE = re.compile(
    TITLE_SESSION_CODE_RE.pattern
    + r"(?:\s*[:|]\s*|\s+)"
    + r"(?:(?:\((?i:invited|plenary|keynote|late news)\))\s+)?"
)

TITLE_SESSION_ONLY_RE = re.compile(TITLE_SESSION_CODE_RE.pattern + r"\s*$")

TITLE_SUPERSCRIPT_MARKER_RE = re.compile(r"^[\d\s,.*†‡§#()]+$")

TITLE_ROLE_PREFIX_RE = re.compile(
    r"^\((?i:invited|plenary|keynote|late news)\)\s*"
)

TITLE_ACCESS_BANNER_RE = re.compile(
    r"^\[[^\]]*(?i:official use|confidential|public|general)[^\]]*\]\s*"
)

TITLE_ISBN_RE = re.compile(
    r"^(?:ISBN(?:-1[03])?:?\s*)?97[89](?:[-\s]?\d){8,}"
)

PERSON_NAME_TOKEN_RE = re.compile(
    r"(?:(?:[A-Z]\.)+|[A-Z]|[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+)"
)

PERSON_NAME_PARTICLES = {
    "da",
    "de",
    "del",
    "der",
    "di",
    "dos",
    "du",
    "la",
    "le",
    "van",
    "von",
}

TITLE_AUTHOR_AMBIGUOUS_WORDS = {
    "analysis",
    "approach",
    "architecture",
    "circuit",
    "circuits",
    "computing",
    "conclusion",
    "control",
    "demonstration",
    "design",
    "discussion",
    "evaluation",
    "experimental",
    "management",
    "memory",
    "method",
    "methods",
    "modeling",
    "operation",
    "performance",
    "power",
    "principle",
    "processor",
    "results",
    "system",
    "systems",
    "technique",
    "techniques",
    "technology",
    "theoretical",
    "understanding",
    "working",
}

AUTHOR_WITH_MARKERS_RE = re.compile(
    r"(?P<prefix>.*?)(?P<authors>"
    r"(?:(?:[A-Z]\.|[A-Z][A-Za-z'’.-]+)\s+"
    r"(?:[A-Z]\.|[A-Z][A-Za-z'’.-]+)\s*\d"
    r"(?:\s*[†*#+]+)?\s*,\s*){2,}.*)$"
)

AUTHOR_NAME_LIST_RE = re.compile(
    r"(?P<prefix>.*?)(?P<authors>"
    r"(?:(?:[A-Z]\.|[A-Z][A-Za-z'’.-]+)\s+"
    r"(?:[A-Z]\.|[A-Z][A-Za-z'’.-]+)\s*,\s*){3,}.*)$"
)

def normalize_space(text: str) -> str:
    # Some PDFs expose supplementary-plane glyphs as raw UTF-16 surrogate
    # code units. Python can hold those units, but UTF-8 cache/JSON writes
    # reject them. Recombine valid pairs and replace only genuinely unpaired
    # units so one malformed glyph cannot permanently block ingestion.
    if any(0xD800 <= ord(char) <= 0xDFFF for char in text):
        text = (
            text.encode("utf-16-le", errors="surrogatepass")
            .decode("utf-16-le", errors="replace")
        )
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()

def _normalized_session_code(value: str) -> str:
    return re.sub(r"[-._\s<>]+", "", value).casefold()

def _title_session_prefix_matches_path(
    value: str,
    source_path: Path | None,
) -> bool:
    if source_path is None:
        return False
    match = TITLE_SESSION_CODE_RE.match(value)
    if not match:
        return False
    source_stem = re.sub(r"\(\d+\)$", "", Path(source_path).stem)
    return _normalized_session_code(match.group(0)) == _normalized_session_code(
        source_stem
    )

def clean_extracted_title(
    value: str | None,
    source_path: Path | None = None,
    *,
    verified_session_prefix: bool = False,
) -> str:
    """Remove a conference code only when independent evidence confirms it."""

    cleaned = normalize_space(value or "")
    if not cleaned:
        return ""
    match = TITLE_SESSION_PREFIX_RE.match(cleaned)
    if match and (
        verified_session_prefix
        or _title_session_prefix_matches_path(cleaned, source_path)
    ):
        cleaned = cleaned[match.end() :].strip(" \t:|–—")
    cleaned = TITLE_ACCESS_BANNER_RE.sub("", cleaned, count=1)
    cleaned = TITLE_ROLE_PREFIX_RE.sub("", cleaned, count=1)
    return cleaned

def is_title_boilerplate(value: str) -> bool:
    cleaned = normalize_space(value)
    lowered = cleaned.casefold()
    if not cleaned:
        return True
    if any(lowered.startswith(prefix) for prefix in TITLE_BOILERPLATE_PREFIXES):
        return True
    if TITLE_ISBN_RE.match(cleaned):
        return True
    return bool(
        "©" in cleaned
        or "copyright" in lowered
        or "digest of technical papers" in lowered
        or "proceedings of" in lowered
        or re.fullmatch(r"\(?\s*(?:invited|plenary|keynote|late news)\s*\)?", lowered)
    )

def credible_metadata_title(
    value: str | None,
    source_path: Path | None = None,
) -> bool:
    if not value:
        return False
    cleaned = clean_extracted_title(value, source_path)
    lowered = cleaned.lower()
    return (
        10 <= len(cleaned) <= 320
        and not any(x in lowered for x in BAD_METADATA_TITLES)
        and not WORKING_METADATA_TITLE_RE.search(cleaned)
        and not has_title_text_corruption(cleaned)
    )

def is_standalone_title_session_code(value: str) -> bool:
    return bool(TITLE_SESSION_ONLY_RE.fullmatch(normalize_space(value)))

def _looks_like_comma_separated_author_names(line: str) -> bool:
    if "," not in line:
        return False
    line = re.sub(
        r"(?<=[A-Za-zÀ-ÖØ-öø-ÿ])\d+(?:,\d+)*",
        "",
        line,
    )
    segments = [
        segment.strip()
        for segment in re.split(r"\s*,\s*|\s+(?i:and)\s+|\s*&\s*", line)
        if segment.strip()
    ]
    if len(segments) < 2:
        return False
    for index, segment in enumerate(segments):
        cleaned = re.sub(r"^[*†‡#+\d\s]+|[*†‡#+\d\s]+$", "", segment)
        cleaned = re.sub(r"^(?i:and)\s+", "", cleaned)
        words = cleaned.split()
        partial_final_name = (
            index == len(segments) - 1
            and len(segments) >= 4
            and len(words) == 1
        )
        if not (partial_final_name or 2 <= len(words) <= 5):
            return False
        if not all(
            word.casefold() in PERSON_NAME_PARTICLES
            or PERSON_NAME_TOKEN_RE.fullmatch(word)
            for word in words
        ):
            return False
        if any(
            word.casefold().strip(".,:;()")
            in TITLE_AUTHOR_AMBIGUOUS_WORDS
            for word in words
        ):
            return False
    return True

def _looks_like_possible_author_name_line(line: str) -> bool:
    if _looks_like_comma_separated_author_names(line):
        return True
    line = re.sub(
        r"(?<=[A-Za-zÀ-ÖØ-öø-ÿ])\d+(?:,\d+)*",
        "",
        line,
    )
    segments = [
        segment.strip()
        for segment in re.split(r"\s+(?i:and)\s+|\s*&\s*", line)
        if segment.strip()
    ]
    if not 1 <= len(segments) <= 4:
        return False
    for segment in segments:
        cleaned = re.sub(r"^[*†‡#+\d\s]+|[*†‡#+\d\s]+$", "", segment)
        words = cleaned.split()
        if not 2 <= len(words) <= 5:
            return False
        if not all(
            word.casefold() in PERSON_NAME_PARTICLES
            or PERSON_NAME_TOKEN_RE.fullmatch(word)
            for word in words
        ):
            return False
        if any(
            word.casefold().strip(".,:;()")
            in TITLE_AUTHOR_AMBIGUOUS_WORDS
            for word in words
        ):
            return False
    return True

def looks_like_affiliation_line(line: str) -> bool:
    lowered = line.casefold()
    if any(marker in lowered for marker in AFFILIATION_MARKERS) or "@" in line:
        return True
    country_or_region = re.search(
        r"\b(?:australia|belgium|canada|china|france|germany|india|"
        r"ireland|italy|japan|korea|singapore|spain|sweden|switzerland|"
        r"taiwan|uk|usa|united kingdom|united states)\b",
        lowered,
    )
    return bool(
        line.count(",") >= 2
        and (country_or_region or ("(" in line and ")" in line))
    )

def looks_like_author_line(line: str) -> bool:
    lowered = line.lower()
    if any(marker in lowered for marker in AFFILIATION_MARKERS):
        return True
    if "@" in line:
        return True
    if AUTHOR_WITH_MARKERS_RE.match(line):
        return True
    name_list_match = AUTHOR_NAME_LIST_RE.match(line)
    if (
        name_list_match
        and _looks_like_comma_separated_author_names(
            name_list_match.group("authors")
        )
    ):
        return True
    if _looks_like_comma_separated_author_names(line):
        return True
    comma_count = line.count(",")
    initial_count = len(re.findall(r"\b[A-Z]\.", line))
    names_with_markers = len(
        re.findall(r"\b[A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){1,3}\s*\d", line)
    )
    return comma_count >= 2 and (
        initial_count >= 1 or names_with_markers >= 2
    )

def split_inline_authors(line: str) -> tuple[str, bool]:
    for pattern in (AUTHOR_WITH_MARKERS_RE, AUTHOR_NAME_LIST_RE):
        match = pattern.match(line)
        if (
            match
            and pattern is AUTHOR_NAME_LIST_RE
            and not _looks_like_comma_separated_author_names(
                match.group("authors")
            )
        ):
            continue
        if match and len(match.group("prefix").strip()) >= 10:
            return match.group("prefix").strip(), True
    return line, False

def has_text_encoding_problem(text: str) -> bool:
    if not text:
        return False
    control_chars = sum(
        1 for char in text if ord(char) < 32 and char not in {"\n", "\r", "\t"}
    )
    return control_chars / max(len(text), 1) > 0.05

def has_title_text_corruption(value: str) -> bool:
    """Detect severe PDF glyph duplication without rejecting technical notation."""

    compact = [char.casefold() for char in value if not char.isspace()]
    if len(compact) < 20:
        return False
    adjacent_duplicates = sum(
        1
        for previous, current in zip(compact, compact[1:])
        if previous == current and current.isalnum()
    )
    return adjacent_duplicates >= 8 and adjacent_duplicates / len(compact) >= 0.1

def _plausible_title(value: str) -> bool:
    lowered = value.casefold()
    return bool(
        10 <= len(value) <= 320
        and len(value.split()) <= 45
        and not lowered.startswith("abstract")
        and not is_title_boilerplate(value)
        and not looks_like_author_line(value)
        and not has_title_text_corruption(value)
    )

def extract_title(
    first_page_text: str,
    metadata_title: str | None,
    source_path: Path | None = None,
) -> tuple[str, str, float]:
    lines = [normalize_space(line) for line in first_page_text.splitlines()]
    lines = [
        line
        for line in lines
        if line
        and not is_standalone_title_session_code(line)
        and not is_title_boilerplate(line)
        and not (
            len(line) <= 12
            and TITLE_SUPERSCRIPT_MARKER_RE.fullmatch(line)
        )
    ]
    title_lines: list[str] = []

    candidate_lines = lines[:12]
    for index, line in enumerate(candidate_lines):
        if re.search(r"\babstract\b", line, re.IGNORECASE):
            break
        if title_lines and looks_like_author_line(line):
            break
        if title_lines and _looks_like_possible_author_name_line(line):
            next_line = (
                candidate_lines[index + 1]
                if index + 1 < len(candidate_lines)
                else ""
            )
            if next_line and looks_like_affiliation_line(next_line):
                break
        if looks_like_affiliation_line(line):
            break
        title_part, found_inline_authors = split_inline_authors(line)
        title_lines.append(title_part)
        if found_inline_authors:
            break
        if len(" ".join(title_lines)) > 300:
            break

    derived = clean_extracted_title(" ".join(title_lines), source_path)
    if _plausible_title(derived):
        return derived, "first_page", 0.85
    if credible_metadata_title(metadata_title, source_path):
        return (
            clean_extracted_title(metadata_title, source_path),
            "pdf_metadata",
            0.65,
        )
    return "", "unresolved", 0.0

def extract_abstract(first_page_text: str) -> str:
    text = normalize_space(first_page_text)
    match = ABSTRACT_RE.search(text)
    if match:
        return normalize_space(match.group("body"))

    marker = re.search(r"\bAb\s*stract\s*[-—:]?\s*", text, re.IGNORECASE)
    if not marker:
        intro = FIRST_SECTION_RE.search(text)
        if not intro:
            return ""
        prefix_lines = text[: intro.start()].splitlines()
        last_affiliation = -1
        for index, line in enumerate(prefix_lines):
            lowered = line.lower()
            if "@" in line or any(marker in lowered for marker in AFFILIATION_MARKERS):
                last_affiliation = index
        candidate = normalize_space("\n".join(prefix_lines[last_affiliation + 1 :]))
        return candidate if 100 <= len(candidate) <= 4000 else ""
    tail = text[marker.end() :]
    intro = FIRST_SECTION_RE.search(tail)
    if intro:
        tail = tail[: intro.start()]
    tail = normalize_space(tail)
    if len(tail) <= 4000:
        return tail
    complete_sentences = list(re.finditer(r"[.!?](?=\s|$)", tail[:4000]))
    return (
        tail[: complete_sentences[-1].end()].strip()
        if complete_sentences
        else ""
    )

