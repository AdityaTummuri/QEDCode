"""Tiny CSV helpers."""


def parse_csv_line(line):
    """Split one CSV line into trimmed fields. Lines starting with # are comments."""
    line = line.strip()
    if line[0] == "#":
        return []
    return [field.strip() for field in line.split(",")]


def parse_csv_rows(text):
    """Parse several lines, skipping comment lines."""
    rows = []
    for line in text.splitlines():
        fields = parse_csv_line(line) if line.strip() else []
        if fields:
            rows.append(fields)
    return rows
