import re


def sort_key_by_lex_and_num_ordered(x):
    """
    Build a sort key that orders strings lexicographically while keeping
    embedded numbers in natural numeric order.

    Splits the string on digit runs so that e.g. 'item2' sorts before
    'item10' instead of after it.

    ### Args

    - **x** (str): The string to derive the sort key from; a non-string
        input raises TypeError via re.split

    ### Returns

    - **list**: Mixed list of ints and strings, suitable as a sorted() key

    ### Notes

    : Intended for use as the ``key`` argument of sorted() or list.sort()

    """
    return [int(c) if c.isdigit() else c for c in re.split(r'(\d+)', x)]
