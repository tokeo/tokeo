import re

# sort by lexicographically order and respect numbers in strings while ordering
SORT_KEY_BY_LEX_NUM_ORDER_LAMBDA = lambda x:[int(c) if c.isdigit() else c for c in re.split(r'(\d+)', x)]
