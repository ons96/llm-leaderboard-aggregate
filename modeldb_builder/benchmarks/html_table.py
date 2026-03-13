from __future__ import annotations

from html.parser import HTMLParser


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._in_table = False
        self._in_tr = False
        self._in_cell = False
        self._cell_buf: list[str] = []
        self._row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t == "table":
            self._in_table = True
        if not self._in_table:
            return
        if t == "tr":
            self._in_tr = True
            self._row = []
        if t in ("td", "th") and self._in_tr:
            self._in_cell = True
            self._cell_buf = []

    def handle_endtag(self, tag):
        t = tag.lower()
        if t == "table":
            self._in_table = False
        if not self._in_table and t != "table":
            return
        if t in ("td", "th") and self._in_cell:
            self._in_cell = False
            cell = "".join(self._cell_buf).strip()
            self._row.append(" ".join(cell.split()))
        if t == "tr" and self._in_tr:
            self._in_tr = False
            if self._row:
                self.rows.append(self._row)

    def handle_data(self, data):
        if self._in_cell:
            self._cell_buf.append(data)


def parse_first_html_table(html_text: str) -> list[list[str]]:
    p = _TableParser()
    p.feed(html_text)
    return p.rows

