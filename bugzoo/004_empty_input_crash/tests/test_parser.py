from csvlite.parser import parse_csv_line, parse_csv_rows


def test_splits_and_trims_fields():
    assert parse_csv_line("a, b,c") == ["a", "b", "c"]


def test_comment_lines_are_skipped():
    assert parse_csv_line("# a comment") == []


def test_a_single_field():
    assert parse_csv_line("single") == ["single"]


def test_rows_skip_blank_and_comment_lines():
    assert parse_csv_rows("a,b\n\n# note\nc,d") == [["a", "b"], ["c", "d"]]
