"""fts_query stops collecting at max_terms, so a pasted 150 KB message does not make every retrieval quadratic."""
import time

from personal_os.repos import fts_query


def test_large_input_is_fast_and_unchanged():
    text = "\n".join(f"const v{i} = {i} // line {i}" for i in range(20000))
    t = time.time()
    q = fts_query(text)
    assert time.time() - t < 0.5
    assert q.count(" OR ") == 11
    assert fts_query("alpha beta alpha gamma") == '"alpha" OR "beta" OR "gamma"'


if __name__ == "__main__":
    test_large_input_is_fast_and_unchanged()
    print("ok")
