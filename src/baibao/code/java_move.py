"""Move Java source files between packages: update `package` declaration and
synchronize all `import` references across the source tree.

Common scenarios:
    - Reorganize packages (e.g. consolidate scattered Demo classes into a
      single demo subpackage).
    - Move a class from main sources to test sources (or vice versa).
    - Fix a misplaced test whose package path doesn't match its SUT.

The mover supports multiple source roots (e.g. ``src/main/java`` and
``src/test/java``) so import references in both trees are kept in sync.

Usage:
    from baibao.code.java_move import move_java_file, JavaMove

    # One-shot move
    move_java_file(
        src_relpath="store/code/csv/ApacheCsvDemo.java",
        dest_relpath="store/code/demo/csv/ApacheCsvDemo.java",
        src_roots=["src/main/java", "src/test/java"],
    )

    # Batch move with a list of JavaMove namedtuples
    moves = [
        JavaMove("store/code/csv/ApacheCsvDemo.java",
                 "store/code/demo/csv/ApacheCsvDemo.java"),
        ...
    ]
    move_java_batch(moves, src_roots=["src/main/java", "src/test/java"])
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from typing import NamedTuple

__all__ = [
    "JavaMove",
    "derive_package",
    "move_java_batch",
    "move_java_file",
    "path_to_fqcn",
    "update_imports_in_tree",
    "update_package_declaration",
]


_PKG_RE = re.compile(r"^(\s*package\s+)([\w\.]+)(\s*;.*)$", re.MULTILINE)


class JavaMove(NamedTuple):
    """A single Java file move.

    Attributes:
        src_relpath: source path relative to a source root, e.g.
            ``store/code/csv/ApacheCsvDemo.java``.
        dest_relpath: destination path relative to a source root (or to
            ``dest_root`` if given).
        dest_root: optional explicit destination root for cross-root moves
            (e.g. from ``src/main/java`` to ``src/test/java``). ``None`` means
            keep the source root.
    """
    src_relpath: str
    dest_relpath: str
    dest_root: str | None = None


def path_to_fqcn(relpath: str) -> str:
    """``store/code/csv/ApacheCsvDemo.java`` -> ``store.code.csv.ApacheCsvDemo``."""
    p = relpath.replace("\\", "/").lstrip("/")
    p = p.removesuffix(".java")
    return p.replace("/", ".")


def derive_package(dest_relpath: str) -> str:
    """``store/code/demo/csv/ApacheCsvDemo.java`` -> ``store.code.demo.csv``."""
    p = dest_relpath.replace("\\", "/").lstrip("/")
    d = os.path.dirname(p)
    return d.replace("/", ".")


def update_package_declaration(file_path: str, new_package: str) -> bool:
    """Rewrite the ``package`` declaration in file_path.

    Returns True if the file was actually changed.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    new_content = _PKG_RE.sub(
        lambda m: m.group(1) + new_package + m.group(3),
        content,
        count=1,
    )
    if new_content != content:
        with open(file_path, "w", encoding="utf-8", newline="") as f:
            f.write(new_content)
        return True
    return False


def update_imports_in_tree(src_root: str, old_fqcn: str, new_fqcn: str) -> int:
    """Scan all .java files under src_root; replace ``import old_fqcn;`` with
    ``new_fqcn;``. Also rewrites inline fully-qualified usages (rare).

    Returns the number of files changed.
    """
    if old_fqcn == new_fqcn:
        return 0
    changed = 0
    old_imp = "import " + old_fqcn + ";"
    new_imp = "import " + new_fqcn + ";"
    fqcn_pattern = re.compile(r"\b" + re.escape(old_fqcn) + r"\b")
    for dirpath, _, files in os.walk(src_root):
        for name in files:
            if not name.endswith(".java"):
                continue
            full = os.path.join(dirpath, name)
            with open(full, "r", encoding="utf-8") as f:
                content = f.read()
            new_content = content
            if old_imp in new_content:
                new_content = new_content.replace(old_imp, new_imp)
            # Inline FQCN (only when no matching import statement remains)
            if old_fqcn in new_content and old_imp not in new_content:
                new_content = fqcn_pattern.sub(new_fqcn, new_content)
            if new_content != content:
                with open(full, "w", encoding="utf-8", newline="") as f:
                    f.write(new_content)
                changed += 1
    return changed


def _find_source_file(src_relpath: str, src_roots: Sequence[str]) -> tuple[str, str] | None:
    """Locate src_relpath under one of src_roots. Returns (abs_path, src_root)
    or None if not found."""
    for root in src_roots:
        candidate = os.path.join(root, src_relpath.lstrip("/\\"))
        if os.path.exists(candidate):
            return candidate, root
    return None


def move_java_file(
    src_relpath: str,
    dest_relpath: str,
    src_roots: Sequence[str],
    *,
    dest_root: str | None = None,
    dry_run: bool = False,
) -> bool:
    """Move a single Java file between packages.

    Args:
        src_relpath: source path relative to a src_root.
        dest_relpath: destination path relative to target_root.
        src_roots: all source roots to scan for import updates (e.g. both
            ``src/main/java`` and ``src/test/java``).
        dest_root: explicit destination root for cross-root moves. ``None``
            means "same root as source".
        dry_run: if True, no files are touched.

    Returns:
        True if a move would happen / happened, False if source not found or
        src==dest.
    """
    found = _find_source_file(src_relpath, src_roots)
    if not found:
        return False
    src_file, src_root = found
    target_root = dest_root if dest_root else src_root
    dest_file = os.path.join(target_root, dest_relpath.lstrip("/\\"))
    if os.path.abspath(src_file) == os.path.abspath(dest_file):
        return False
    old_fqcn = path_to_fqcn(src_relpath)
    new_fqcn = path_to_fqcn(dest_relpath)
    new_package = derive_package(dest_relpath)
    if dry_run:
        return True
    dest_dir = os.path.dirname(dest_file)
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)
    with open(src_file, "r", encoding="utf-8") as f:
        content = f.read()
    new_content = _PKG_RE.sub(
        lambda m: m.group(1) + new_package + m.group(3),
        content,
        count=1,
    )
    with open(dest_file, "w", encoding="utf-8", newline="") as f:
        f.write(new_content)
    os.remove(src_file)
    for root in src_roots:
        update_imports_in_tree(root, old_fqcn, new_fqcn)
    return True


def move_java_batch(
    moves: Iterable[JavaMove],
    src_roots: Sequence[str],
    *,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Move multiple Java files. Returns (moved_count, skipped_count).

    Logs each move via the ``baibao`` logger (use pykunlun.util.logutil).
    """
    from pykunlun.util import logutil
    log = logutil.getLogger(__name__)
    ok = skipped = 0
    for m in moves:
        found = _find_source_file(m.src_relpath, src_roots)
        if not found:
            log.warning("[SKIP] source not found: %s", m.src_relpath)
            skipped += 1
            continue
        _, src_root = found
        target_root = m.dest_root if m.dest_root else src_root
        log.info("[MOVE] %s (%s) -> %s (%s)",
                 m.src_relpath, os.path.basename(src_root),
                 m.dest_relpath, os.path.basename(target_root))
        moved = move_java_file(
            m.src_relpath, m.dest_relpath, src_roots,
            dest_root=m.dest_root, dry_run=dry_run,
        )
        if moved:
            ok += 1
        else:
            skipped += 1
    log.info("Done. %d moved, %d skipped.", ok, skipped)
    return ok, skipped
