from csvlite.parser import parse_csv_line


def test_empty_line_gives_an_empty_list():
    assert parse_csv_line("") == []


def test_whitespace_only_line_gives_an_empty_list():
    assert parse_csv_line("   ") == []
