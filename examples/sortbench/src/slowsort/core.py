"""Deliberately quadratic: list membership for dedupe and an insertion sort."""


def dedupe_and_sort(values: list[int]) -> list[int]:
    """Return the unique values in ascending order, keeping first occurrences."""
    unique: list[int] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    result: list[int] = []
    for value in unique:
        index = 0
        while index < len(result) and result[index] < value:
            index += 1
        result.insert(index, value)
    return result
