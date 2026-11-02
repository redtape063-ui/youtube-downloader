"""
Patch for xhtml2pdf / reportlab_paragraph bug:
In Python 3, when a paragraph has a single fragment and a long word is split
(splitLongWords), _splitLongWords returns `str` pieces instead of `bytes`.
When _leftDrawParaLine (or center/right/justify) tries to call `b" ".join(words)`,
it raises:
`TypeError: sequence item 0: expected a bytes-like object, str found`

This module automatically monkey-patches xhtml2pdf.reportlab_paragraph to ensure
all words passed into line drawing functions are encoded as bytes.
"""

def apply_patch():
    try:
        import xhtml2pdf.reportlab_paragraph as rlp

        orig_left = rlp._leftDrawParaLine
        orig_center = rlp._centerDrawParaLine
        orig_right = rlp._rightDrawParaLine
        orig_justify = rlp._justifyDrawParaLine

        def _safe_words(words):
            if not words:
                return words
            safe = []
            for w in words:
                if isinstance(w, str):
                    safe.append(w.encode('utf-8'))
                else:
                    safe.append(w)
            return safe

        def patched_left(tx, offset, extraspace, words, _last=0):
            return orig_left(tx, offset, extraspace, _safe_words(words), _last)

        def patched_center(tx, offset, extraspace, words, _last=0):
            return orig_center(tx, offset, extraspace, _safe_words(words), _last)

        def patched_right(tx, offset, extraspace, words, _last=0):
            return orig_right(tx, offset, extraspace, _safe_words(words), _last)

        def patched_justify(tx, offset, extraspace, words, last=0):
            return orig_justify(tx, offset, extraspace, _safe_words(words), last)

        rlp._leftDrawParaLine = patched_left
        rlp._centerDrawParaLine = patched_center
        rlp._rightDrawParaLine = patched_right
        rlp._justifyDrawParaLine = patched_justify

    except Exception as e:
        print(f"Failed to apply xhtml2pdf paragraph patch: {e}")

    # Also patch xhtml2pdf.tables.TableData.get_data and pisaTagTD.end
    # so that colspan/rowspan across multiple columns in a single row work properly:
    # 1. In pisaTagTD.end: `tdata.col` must advance by `cspan` (not just 1 via add_cell),
    #    otherwise subsequent cells in the same row get wrong column indices!
    # 2. In TableData.get_data: `self.span` must be sorted by `(y, x)` so that
    #    `data[y].insert(x, "")` inserts empty placeholders from left to right!
    try:
        import xhtml2pdf.tables as xtables

        orig_get_data = xtables.TableData.get_data

        def patched_get_data(self):
            self.span.sort(key=lambda pos: (pos[1], pos[0]))
            return orig_get_data(self)

        xtables.TableData.get_data = patched_get_data

        orig_td_start = xtables.pisaTagTD.start
        orig_td_end = xtables.pisaTagTD.end

        def patched_td_start(self, c):
            # Fix while loop in pisaTagTD.start so it skips all spanned columns, not just 1!
            tdata = c.tableData
            row = tdata.row
            col = tdata.col
            span_set = set(tdata.span)
            while (col, row) in span_set:
                col += 1
                tdata.col += 1
            return orig_td_start(self, c)

        def patched_td_end(self, c):
            orig_td_end(self, c)
            cspan = self.attr.colspan
            if cspan and cspan > 1:
                c.tableData.col += (cspan - 1)

        xtables.pisaTagTD.start = patched_td_start
        xtables.pisaTagTD.end = patched_td_end

    except Exception as e:
        print(f"Failed to apply xhtml2pdf tables patch: {e}")

# Apply immediately upon import
apply_patch()
