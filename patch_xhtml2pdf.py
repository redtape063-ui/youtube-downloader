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
        print(f"Failed to apply xhtml2pdf patch: {e}")

# Apply immediately upon import
apply_patch()
