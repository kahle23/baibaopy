"""Mojibake detection and restoration for source files.

Handles the most common Chinese-source-file mojibake type:
**UTF-8 bytes were read as GBK/CP936 and re-saved as UTF-8**. This produces
text that looks like garbled CJK (e.g. "鎸囧畾闆嗙兢鍚嶇О") and can be
restored by round-tripping: encode the mojibake text back via GB18030 and
decode as UTF-8.

Why GB18030 (not GBK)?
    GBK cannot encode characters in the PUA (Private Use Area, U+E000-U+F8FF).
    Windows CP936 maps some byte sequences that have no legal GBK double-byte
    representation to PUA code points. GB18030 is a superset of GBK that covers
    all Unicode code points, so it can round-trip these PUA chars back to the
    original UTF-8 bytes.

Algorithm summary:
    1. Split each line into runs of non-ASCII chars (with '?' absorption, since
       trailing 0x3F often marks an isolated byte from the original UTF-8).
    2. For each run containing at least one "weird" char (rare Unicode ranges
       that mojibake artifacts land in), try GB18030-encode + UTF-8-decode.
    3. Accept the restoration if it strictly reduces the "weird" count, yields
       >= 2 consecutive CJK Unified Ideographs, and produces <= 2 U+FFFD.

Usage:
    from baibao.text.mojibake import scan_file, fix_file, looks_mojibake

    suspicious = scan_file("path/to/File.java")        # detect only
    fixed_count = fix_file("path/to/File.java")        # fix in place
    fixed_count = fix_file("path/to/File.java", dry_run=True)  # preview
"""

from __future__ import annotations

import os

__all__ = [
    "clean_fffd",
    "clean_fffd_file",
    "fix_file",
    "fix_text",
    "looks_mojibake",
    "scan_file",
    "scan_tree",
]


# ---------------------------------------------------------------------------
# Char classification helpers
# ---------------------------------------------------------------------------

def _is_cjk_unified(ch: str) -> bool:
    """CJK Unified Ideographs (common Chinese chars)."""
    cp = ord(ch)
    return 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF


def _is_non_ascii(ch: str) -> bool:
    """Non-ASCII char (>= 0x80). Mojibake artifacts span many Unicode ranges
    and must stay together as a run to preserve byte continuity."""
    return ord(ch) >= 0x80


def _is_in_gb2312(ch: str) -> bool:
    """Whether ch is in the GB2312 charset (Python's gb2312 codec).

    NOTE: Python's 'gb2312' codec is actually permissive and accepts most GBK
    chars, so this is a coarse filter. Used to distinguish common chars that
    happen to fall in the 0x9000-0x9FFF range (e.g. 间 U+95F4) from rare
    mojibake artifacts in the same range (e.g. 鐢 U+9422).
    """
    try:
        ch.encode("gb2312")
        return True
    except UnicodeEncodeError:
        return False


def _is_weird_char(ch: str) -> bool:
    """Whether ch is a "rare/overflow" Unicode char produced by mojibake.

    When UTF-8 bytes are interpreted as GBK, byte pairs often land in rare ranges:
    CJK Radicals/Kangxi (2E80-2FFF), Hiragana+Katakana (3040-30FF, never in
    Chinese text), Bopomofo (3100-31BF), CJK Compat (3200-32FF), the rare CJK
    range 0x9000-0x9FFF, Yi (A000-A48F), PUA (E000-F8FF), CJK Compat
    Ideographs (F900-FAFF), CJK Compat Forms (FE30-FE4F).
    """
    cp = ord(ch)
    if ((0x2E80 <= cp <= 0x2FFF) or
        (0x3040 <= cp <= 0x30FF) or  # Hiragana + Katakana
        (0x3100 <= cp <= 0x31BF) or  # Bopomofo
        (0x3200 <= cp <= 0x32FF) or  # CJK Compat
        (0xA000 <= cp <= 0xA48F) or  # Yi
        (0xE000 <= cp <= 0xF8FF) or  # PUA
        (0xF900 <= cp <= 0xFAFF) or  # CJK Compat Ideographs
        (0xFE30 <= cp <= 0xFE4F)):   # CJK Compat Forms
        return True
    # 0x9000-0x9FFF: only weird if NOT in gb2312 (excludes common chars like 间/类)
    return 0x9000 <= cp <= 0x9FFF and not _is_in_gb2312(ch)


def _has_consecutive_cjk(text: str, n: int = 2) -> bool:
    """Whether text has at least n consecutive CJK Unified Ideographs."""
    run = 0
    for ch in text:
        if _is_cjk_unified(ch):
            run += 1
            if run >= n:
                return True
        else:
            run = 0
    return False


def _score(s: str) -> tuple[int, int, int]:
    """Return (weird_count, cjk_unified_count, fffd_count)."""
    w = c = f = 0
    for ch in s:
        cp = ord(ch)
        if _is_weird_char(ch):
            w += 1
        if 0x4E00 <= cp <= 0x9FFF:
            c += 1
        if cp == 0xFFFD:
            f += 1
    return w, c, f


# ---------------------------------------------------------------------------
# Run extraction & round-trip restoration
# ---------------------------------------------------------------------------

def _extract_non_ascii_runs(text: str, min_len: int = 2) -> list[str]:
    """Split text into runs of consecutive non-ASCII chars (len >= min_len).

    Special rule: when '?' (0x3F) immediately follows a non-ASCII char, include
    it in the current run. Reason: when UTF-8 is read as GBK, the trailing byte
    of original Chinese often ends up as an isolated 0x3F; treating it as a
    separator breaks byte continuity and prevents restoration.
    """
    runs: list[str] = []
    cur: list[str] = []
    for ch in text:
        if _is_non_ascii(ch) or ch == "?" and cur and _is_non_ascii(cur[-1]):
            cur.append(ch)
        else:
            if len(cur) >= min_len:
                runs.append("".join(cur))
            cur = []
    if len(cur) >= min_len:
        runs.append("".join(cur))
    return runs


def _try_roundtrip_segment(seg: str) -> tuple[str, bool]:
    """Round-trip a segment via GB18030 encode + UTF-8 decode.

    Returns (restored_text, success). GB18030 covers all Unicode code points
    (including PUA), so it can restore segments with PUA chars that GBK rejects.

    Success criteria (compared via score):
    - weird count strictly decreases (excludes false positives like "摘要"->"噪勪"
      where both sides have 0 weird)
    - restored text has >= 2 consecutive CJK Unified Ideographs
    - U+FFFD count <= 2 (allows for some byte loss at segment boundaries)
    """
    orig_score = _score(seg)
    best = seg
    for enc_err, dec_err in [("strict", "strict"), ("replace", "strict"),
                              ("strict", "replace"), ("replace", "replace")]:
        try:
            b = seg.encode("gb18030", errors=enc_err)
            rt = b.decode("utf-8", errors=dec_err)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if rt == seg:
            continue
        rt_score = _score(rt)
        if (rt_score[0] < orig_score[0] and
                _has_consecutive_cjk(rt, 2) and
                rt_score[2] <= 2):
            best = rt
            orig_score = rt_score
    return best, best != seg


# Fixed mojibake patterns that the "weird char" heuristic misses because the
# resulting char is in GB2312. Map them directly.
# Example: U+FF1A '：' (fullwidth colon) -> UTF-8 EF BC 9A -> GBK reads
# EF BC as 锛 + leftover 9A as '?'.
_FIXED_PATTERNS = {
    "锛?": "：",  # U+FF1A fullwidth colon
}


def looks_mojibake(text: str) -> bool:
    """Whether text contains mojibake.

    Strategy: split into non-ASCII runs (with '?' absorption), only attempt
    restoration on runs containing at least one weird char. This handles
    "line mixing mojibake + correct Chinese" without damaging correct segments.
    Also detects fixed patterns like '锛?'.
    """
    if not text:
        return False
    if any(p in text for p in _FIXED_PATTERNS):
        return True
    for run in _extract_non_ascii_runs(text):
        if not any(_is_weird_char(ch) for ch in run):
            continue  # skip correct-Chinese runs to avoid false positives
        _, ok = _try_roundtrip_segment(run)
        if ok:
            return True
    return False


def fix_text(text: str) -> tuple[str, int]:
    """Fix one chunk of text. Returns (new_text, replacement_count).

    Step 1: apply fixed patterns (e.g. '锛?' -> '：').
    Step 2: round-trip restoration on remaining weird-char runs.
    """
    if not text:
        return text, 0
    new_text = text
    count = 0
    for bad, good in _FIXED_PATTERNS.items():
        if bad in new_text:
            new_text = new_text.replace(bad, good)
            count += 1
    if looks_mojibake(new_text):
        for run in _extract_non_ascii_runs(new_text):
            if not any(_is_weird_char(ch) for ch in run):
                continue
            rt, ok = _try_roundtrip_segment(run)
            if ok:
                new_text = new_text.replace(run, rt, 1)
                count += 1
    return new_text, count


# ---------------------------------------------------------------------------
# File / tree operations
# ---------------------------------------------------------------------------

def scan_file(path: str) -> tuple[list[tuple[int, str]], int]:
    """Scan one file. Returns (list of (lineno, line), total_lines)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError):
        return [], 0
    suspicious: list[tuple[int, str]] = []
    for i, line in enumerate(lines, 1):
        if looks_mojibake(line):
            suspicious.append((i, line.rstrip("\n")))
    return suspicious, len(lines)


def scan_tree(root: str, suffix: str = ".java") -> list[tuple[str, list[tuple[int, str]], int]]:
    """Scan all files under root with given suffix. Returns list of
    (path, suspicious_lines, total_lines) for files with mojibake."""
    out: list[tuple[str, list[tuple[int, str]], int]] = []
    for dirpath, _, files in os.walk(root):
        for name in files:
            if not name.endswith(suffix):
                continue
            full = os.path.join(dirpath, name)
            sus, total = scan_file(full)
            if sus:
                out.append((full, sus, total))
    return out


def fix_file(path: str, dry_run: bool = False) -> int:
    """Fix one file in place. Returns the number of replacements applied."""
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    total = 0
    new_lines: list[str] = []
    for line in lines:
        new_line, cnt = fix_text(line)
        total += cnt
        new_lines.append(new_line)
    if total == 0 or dry_run:
        return total
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.writelines(new_lines)
    return total


# ---------------------------------------------------------------------------
# U+FFFD residual cleanup (post-restoration)
# ---------------------------------------------------------------------------

def clean_fffd(text: str, mode: str = "conservative") -> tuple[str, int]:
    """Clean U+FFFD residuals left after round-trip restoration.

    These residuals are unavoidable: when the original UTF-8 byte was lost
    during mojibake (e.g. Windows CP936 replaced isolated byte 0x8D with
    0x3F '?'), the byte information is permanently gone. This function
    cleans the visual artifact (U+FFFD chars) using heuristic rules.

    Modes:
        - "conservative" (default): drops lone U+FFFD; keeps `U+FFFD + '?'`
          pairs unchanged (safer; the '?' still indicates "info was lost here").
        - "aggressive": also replaces `U+FFFD + '?'` with '：' (fullwidth colon).
          Based on the observation that ~80%+ of these pairs are residuals of
          U+FF1A '：' (UTF-8 EF BC 9A, last byte 9A isolated as '?'). May produce
          wrong punctuation for the other ~20% (e.g. should be '！' or '。').
          Use when visual cleanliness matters more than exact punctuation.

    Returns (new_text, replaced_count).
    """
    new_text = text
    count = 0
    if mode == "aggressive" and "\ufffd?" in new_text:
        new_text = new_text.replace("\ufffd?", "：")
        count += 1
    # Drop remaining lone U+FFFD (info already lost, just clean the artifact)
    if "\ufffd" in new_text:
        n_before = len(new_text)
        new_text = new_text.replace("\ufffd", "")
        count += n_before - len(new_text)
    return new_text, count


def clean_fffd_file(path: str, mode: str = "conservative", dry_run: bool = False) -> int:
    """Clean U+FFFD residuals in one file. Returns total replacements."""
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    total = 0
    new_lines: list[str] = []
    for line in lines:
        new_line, cnt = clean_fffd(line, mode=mode)
        total += cnt
        new_lines.append(new_line)
    if total == 0 or dry_run:
        return total
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.writelines(new_lines)
    return total
