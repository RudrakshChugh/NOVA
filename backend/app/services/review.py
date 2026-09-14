def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def normalised_edit_distance(original: str, edited: str) -> float:
    """Levenshtein distance divided by the longer string's length, in [0, 1] (plan §3.3.6)."""
    longest = max(len(original), len(edited))
    return levenshtein(original, edited) / longest if longest else 0.0
