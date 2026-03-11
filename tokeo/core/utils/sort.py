import re


def sort_key_by_lex_and_num_ordered(x):
    """
    Sort by lexicographically order and respect numbers in strings while ordering
    """
    return [int(c) if c.isdigit() else c for c in re.split(r'(\d+)', x)]
