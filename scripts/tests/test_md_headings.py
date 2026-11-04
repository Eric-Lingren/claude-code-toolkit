"""md_headings numbers lines the way git does: by newline only."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "shared"))

import pytest  # noqa: E402

from md_headings import heading_map, iter_headings, split_lines  # noqa: E402

ODD = [" ", " ", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\r"]


def test_split_lines_drops_trailing_empty_only():
    assert split_lines("a\nb\n") == ["a", "b"]
    assert split_lines("a\nb") == ["a", "b"]
    assert split_lines("a\n\n") == ["a", ""]
    assert split_lines("") == []


@pytest.mark.parametrize("ch", ODD)
def test_odd_line_breaks_do_not_shift_line_numbers(ch):
    text = f"# A\nx{ch}y\n## B\nbody\n"
    assert list(iter_headings(text)) == [(1, 1, "A"), (3, 2, "B")]
    by_line = heading_map(text)
    assert len(by_line) == 5
    assert by_line[3] == "## B"
    assert by_line[4] == "## B"
    assert by_line[2] == "# A"
