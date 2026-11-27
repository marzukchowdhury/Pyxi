"""What a workbook's formulas come to, kept in the file Ledger saves.

openpyxl writes a formula without its result. Excel works every formula out as it opens the
file, so nobody saw the gap there; an app that doesn't (a phone's viewer, a preview pane, some
office suites) showed every formula as 0, and a chart of them as a row of empty bars.

Two parts, both plain functions:

``write_results`` puts known results into a saved .xlsx as the ``<v>`` of each formula cell.
Only the worksheet parts change, and in them only those cells: everything else in the file is
the bytes openpyxl wrote. The new file replaces the old one only once it is whole and opens.

``simple_results`` works the simplest formulas out with no office app at all, for when
LibreOffice isn't installed: a product, sum or difference of two cells or numbers, SUM over
plain ranges, and SUMIFS with one plain "equals" condition. It is not a formula evaluator and
must not become one: anything else is left unknown.
"""

from __future__ import annotations

import copy
import logging
import math
import os
import re
import tempfile
import zipfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

log = logging.getLogger(__name__)


class ErrorValue(str):
    """A formula that came to an error: "#DIV/0!", "#N/A"."""


# ============================================================================ into the file
_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
_SHEET_DATA = re.compile(rb"<sheetData>.*?</sheetData>", re.S)
# A cell with something in it (never the self-closing <c r="A1" s="1"/>).
_CELL = re.compile(rb"<c\s([^>]*?[^/>])>(.*?)</c>", re.S)
_REF = re.compile(rb'(?:^|\s)r="([A-Z]{1,3}[0-9]+)"')
_TYPE = re.compile(rb'\s+t="[^"]*"')
# All a formula cell holds as openpyxl writes it: the formula, then an empty result.
_FORMULA = re.compile(rb"\s*(<f\b[^>]*?/>|<f\b[^>]*>[^<]*</f>)\s*(?:<v\s*/>|<v>[^<]*</v>)?\s*", re.S)
_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def cached(value: Any, epoch: Any = None) -> tuple[bytes, bytes] | None:
    """A result as a cell stores it: (its type, its text), or None for one that can't be
    stored (nothing known, not a number at all)."""
    if value is None:
        return None
    if isinstance(value, ErrorValue):
        return b"e", _text(value)
    if isinstance(value, bool):
        return b"b", b"1" if value else b"0"
    if isinstance(value, int):
        return b"", str(value).encode()
    if isinstance(value, float):
        return (b"", repr(value).encode()) if math.isfinite(value) else None
    if isinstance(value, datetime | date | time | timedelta):
        from openpyxl.utils.datetime import to_excel

        kw = {"epoch": epoch} if epoch is not None and not isinstance(value, time | timedelta) else {}
        return b"", repr(float(to_excel(value, **kw))).encode()
    if isinstance(value, str):
        return None if _NOT_XML.search(value) else (b"str", _text(value))
    return None


def _text(s: str) -> bytes:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").encode("utf-8")


def fill_sheet(xml: bytes, results: dict[str, Any], epoch: Any = None) -> tuple[bytes, int]:
    """One worksheet part with these results (by cell, "F2") written into its formula cells:
    the new part, and how many cells were filled. A cell that isn't a formula, or isn't laid
    out the way openpyxl writes one, is left exactly as it is."""
    filled = 0

    def cell(m: re.Match[bytes]) -> bytes:
        nonlocal filled
        attrs, inner = m[1], m[2]
        if b"<f" not in inner:
            return m[0]
        ref = _REF.search(attrs)
        formula = _FORMULA.fullmatch(inner)
        got = cached(results.get(ref[1].decode()), epoch) if ref and formula else None
        if got is None:
            return m[0]
        kind, text = got
        attrs = _TYPE.sub(b"", b" " + attrs)
        if kind:
            attrs += b' t="' + kind + b'"'
        filled += 1
        return b"<c" + attrs + b">" + formula[1] + b"<v>" + text + b"</v></c>"

    def data(m: re.Match[bytes]) -> bytes:
        return _CELL.sub(cell, m[0])

    return _SHEET_DATA.sub(data, xml, count=1), filled


def sheet_parts(z: zipfile.ZipFile) -> dict[str, str]:
    """Which part of the zip each worksheet is, by the sheet's name."""
    targets = {}
    for rel in ElementTree.fromstring(z.read("xl/_rels/workbook.xml.rels")).iter(_PKG):
        target = rel.get("Target", "")
        targets[rel.get("Id")] = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
    book = ElementTree.fromstring(z.read("xl/workbook.xml"))
    names = set(z.namelist())
    return {
        s.get("name", ""): targets[s.get(_REL)]
        for s in book.iter(f"{_MAIN}sheet")
        if targets.get(s.get(_REL)) in names and "/worksheets/" in targets[s.get(_REL)]
    }


def _formulas(wb: Any) -> dict[str, dict[tuple[int, int], str]]:
    return {
        ws.title: {
            at: getattr(c.value, "text", None) or str(c.value)
            for at, c in ws._cells.items()
            if c.data_type == "f"
        }
        for ws in wb.worksheets
    }


def write_results(path: Path, results: dict[str, dict[str, Any]], like: Any = None) -> int:
    """Write formula results ({sheet: {"F2": 419.88}}) into the saved workbook at ``path``,
    and say how many cells got one. ``like``: the workbook as openpyxl holds it, whose
    formulas the new file must still have, every one.

    The file is never lost: the new one is written beside it, opened both ways (formulas and
    values), and only then put in its place. On any failure the file stays as it was saved
    and 0 comes back."""
    from openpyxl import load_workbook

    path = Path(path)
    tmp: Path | None = None
    try:
        epoch = getattr(like, "epoch", None)
        changed: dict[str, bytes] = {}
        total = 0
        with zipfile.ZipFile(path) as zin:
            parts = sheet_parts(zin)
            for title, cells in results.items():
                part = parts.get(title)
                if not part or not cells:
                    continue
                new, n = fill_sheet(zin.read(part), cells, epoch)
                if n:
                    changed[part], total = new, total + n
            if not changed:
                return 0
            # Beside the file (so putting it in place is one rename, on the same drive), and
            # with the file's own ending: openpyxl won't open any other.
            handle, name = tempfile.mkstemp(prefix=".pyxi-", suffix=path.suffix, dir=path.parent)
            os.close(handle)
            tmp = Path(name)
            with zipfile.ZipFile(tmp, "w") as zout:
                zout.comment = zin.comment
                for info in zin.infolist():
                    body = changed.get(info.filename)
                    zout.writestr(copy.copy(info), zin.read(info) if body is None else body)
        with zipfile.ZipFile(tmp) as z:
            if z.testzip() is not None:
                raise ValueError("the new file is damaged")
        again = load_workbook(tmp)
        if like is not None and _formulas(again) != _formulas(like):
            raise ValueError("a formula changed")
        valued = load_workbook(tmp, data_only=True)
        if valued.sheetnames != again.sheetnames:
            raise ValueError("a sheet went missing")
        os.replace(tmp, path)
        tmp = None
        return total
    except Exception:
        log.info("ledger: results not written into %s; it stays as saved", path.name, exc_info=True)
        return 0
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                log.info("ledger: couldn't clear %s", tmp.name, exc_info=True)


# ============================================================================ the simplest sums
class _Unknown(Exception):
    """A formula this can't work out (and so doesn't guess)."""


_SHEET = r"(?:(?:'((?:[^']|'')+)'|([A-Za-z_][A-Za-z0-9_.]*))!)?"
_ONE = rf"{_SHEET}\$?([A-Za-z]{{1,3}})\$?([0-9]{{1,7}})"
_SPAN = rf"{_ONE}:\$?([A-Za-z]{{1,3}})\$?([0-9]{{1,7}})"
_NUMBER = r"[0-9]+(?:\.[0-9]+)?"
_CELL_REF = re.compile(_ONE)
_SPAN_REF = re.compile(_SPAN)
# One side of =A2*B2, as a whole: a number or a cell (the same as _ONE, kept in one piece).
_SIDE = rf"(?:{_NUMBER}|(?:(?:'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_.]*)!)?\$?[A-Za-z]{{1,3}}\$?[0-9]{{1,7}})"
_TWO = re.compile(rf"=\s*({_SIDE})\s*([*+\-])\s*({_SIDE})\s*")
_SUM = re.compile(r"=\s*SUM\s*\((.*)\)\s*", re.I | re.S)
_SUMIFS = re.compile(r"=\s*SUMIFS\s*\((.*)\)\s*", re.I | re.S)
_TEXT = re.compile(r'"([^"]*)"')
_MOST = 200_000  # cells one formula may read: past this it isn't the simple case


def _looks_like_number(s: str) -> bool:
    try:
        float(s.strip().replace(",", "").rstrip("%"))
    except ValueError:
        return False
    return True


class _Simple:
    def __init__(self, wb: Any, known: Any = None) -> None:
        self.wb = wb
        self.known = known  # (ws, row, column) -> a result already known, or None
        self.sheets = {ws.title.casefold(): ws for ws in wb.worksheets}
        self.done: dict[tuple[str, int, int], Any] = {}
        self.busy: set[tuple[str, int, int]] = set()

    def sheet(self, quoted: str | None, bare: str | None, here: Any) -> Any:
        name = quoted.replace("''", "'") if quoted else bare
        if not name:
            return here
        ws = self.sheets.get(name.casefold())
        if ws is None:
            raise _Unknown
        return ws

    def at(self, ws: Any, r: int, c: int) -> Any:
        """A cell's value, working its formula out first when it has one."""
        cell = ws._cells.get((r, c))
        if cell is None or cell.value is None:
            return None
        if cell.data_type == "e":
            raise _Unknown
        if cell.data_type != "f":
            return cell.value
        key = (ws.title, r, c)
        if key in self.done:
            return self.done[key]
        if key in self.busy:  # a formula that reads itself
            raise _Unknown
        self.busy.add(key)
        try:
            got = self.known(ws, r, c) if self.known else None
            if got is None:
                if not isinstance(cell.value, str):
                    raise _Unknown  # an array formula
                got = self.formula(ws, cell.value)
        finally:
            self.busy.discard(key)
        self.done[key] = got
        return got

    def number(self, ws: Any, text: str) -> float | int:
        """One side of =A2*B2: a number, or a cell holding one (an empty cell counts as 0)."""
        m = _CELL_REF.fullmatch(text)
        if m is None:
            return float(text) if "." in text else int(text)
        from openpyxl.utils import column_index_from_string

        v = self.at(self.sheet(m[1], m[2], ws), int(m[4]), column_index_from_string(m[3].upper()))
        if v is None:
            return 0
        if isinstance(v, bool) or not isinstance(v, int | float):
            raise _Unknown  # text, a date: Excel has its own rules for those
        return v

    def span(self, ws: Any, text: str) -> tuple[Any, int, int, int, int]:
        from openpyxl.utils import column_index_from_string

        m = _SPAN_REF.fullmatch(text.strip())
        one = None if m else _CELL_REF.fullmatch(text.strip())
        if m is None and one is None:
            raise _Unknown
        m = m or one
        c1, c2 = sorted(column_index_from_string(x.upper()) for x in (m[3], m[5] if one is None else m[3]))
        r1, r2 = sorted((int(m[4]), int(m[6] if one is None else m[4])))
        if (c2 - c1 + 1) * (r2 - r1 + 1) > _MOST:
            raise _Unknown
        return self.sheet(m[1], m[2], ws), r1, c1, r2, c2

    def cells(self, where: tuple[Any, int, int, int, int]) -> list[Any]:
        ws, r1, c1, r2, c2 = where
        return [self.at(ws, r, c) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]

    @staticmethod
    def added(values: list[Any]) -> float | int:
        """SUM's own rule for a range: numbers are added, text and blanks are passed over."""
        nums = []
        for v in values:
            if isinstance(v, ErrorValue):
                raise _Unknown  # the sum of an error is that error
            if v is None or isinstance(v, str | bool):
                continue
            if not isinstance(v, int | float):
                raise _Unknown  # a date or a time: a number to Excel, not worked out here
            nums.append(v)
        if all(isinstance(v, int) for v in nums):
            return sum(nums)
        return math.fsum(nums)

    def formula(self, ws: Any, text: str) -> Any:
        if m := _TWO.fullmatch(text):
            a, b = self.number(ws, m[1]), self.number(ws, m[3])
            return a * b if m[2] == "*" else a + b if m[2] == "+" else a - b
        if m := _SUM.fullmatch(text):
            parts = m[1].split(",")
            return self.added([v for p in parts for v in self.cells(self.span(ws, p))])
        if m := _SUMIFS.fullmatch(text):
            return self.sumifs(ws, m[1])
        raise _Unknown

    def sumifs(self, ws: Any, inside: str) -> float | int:
        """=SUMIFS(values, labels, one label): the pivot Ledger writes. One condition, and
        only "is this text" or "is this number": no ">5", no wildcards."""
        parts = inside.split(",")
        if len(parts) != 3:
            raise _Unknown
        total, labels = self.span(ws, parts[0]), self.span(ws, parts[1])
        if (total[3] - total[1], total[4] - total[2]) != (labels[3] - labels[1], labels[4] - labels[2]):
            raise _Unknown
        want = parts[2].strip()
        if m := _TEXT.fullmatch(want):
            crit: Any = m[1]
        elif re.fullmatch(_NUMBER, want):
            crit = float(want)
        elif m := _CELL_REF.fullmatch(want):
            from openpyxl.utils import column_index_from_string

            crit = self.at(self.sheet(m[1], m[2], ws), int(m[4]), column_index_from_string(m[3].upper()))
        else:
            raise _Unknown
        if isinstance(crit, bool) or not isinstance(crit, str | int | float):
            raise _Unknown
        if isinstance(crit, str) and (
            not crit.strip()
            or crit[0] in "<>="
            or any(ch in crit for ch in "*?~")
            or _looks_like_number(crit)
            or crit.strip().casefold() in ("true", "false")
        ):
            raise _Unknown
        picked = []
        for label, value in zip(self.cells(labels), self.cells(total), strict=True):
            if label is None:
                continue
            if isinstance(label, bool) or not isinstance(label, str | int | float):
                if isinstance(crit, str) and not isinstance(label, bool):
                    continue  # a date is never the text asked for
                raise _Unknown
            if isinstance(crit, str):
                if isinstance(label, str) and label.casefold() == crit.casefold():
                    picked.append(value)
            elif isinstance(label, str):
                if _looks_like_number(label):
                    raise _Unknown  # "5" typed as text: Excel counts it as the number
            elif label == crit:
                picked.append(value)
        return self.added(picked)


def simple_results(wb: Any, known: Any = None) -> dict[str, dict[str, Any]] | None:
    """Every formula in the workbook worked out, as {sheet: {"F2": 419.88}}: or None when even
    one of them is more than the simple cases here (some results and not others would read
    as a finished file). ``known(ws, row, column)``: a result that is known already."""
    from openpyxl.utils import get_column_letter

    work = _Simple(wb, known)
    out: dict[str, dict[str, Any]] = {}
    try:
        for ws in wb.worksheets:
            for (r, c), cell in list(ws._cells.items()):
                if cell.data_type == "f":
                    out.setdefault(ws.title, {})[f"{get_column_letter(c)}{r}"] = work.at(ws, r, c)
    except _Unknown:
        return None
    except RecursionError:  # a chain of formulas thousands long
        return None
    return out
