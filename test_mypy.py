from typing import Any

def test():
    result: Any = None
    row: list[Any] = result.get_next() # type: ignore
    for i in range(len(row)):
        if isinstance(row[i], str):
            row[i] = row[i][1:]
    rows: list[list[Any]] = []
    rows.append(row)
