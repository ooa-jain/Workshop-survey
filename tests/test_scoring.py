import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.scoring import (
    score_job_intelligence, score_job_search, control_mean,
    control_shift, quadrant,
)


def test_ji_all_a_is_zero():
    r = score_job_intelligence(["a", "a", "a", "a", "a"])
    assert r["total"] == 0.0
    assert r["raw"] == 0

def test_ji_all_d_is_hundred():
    r = score_job_intelligence(["d", "d", "d", "d", "d"])
    assert r["total"] == 100.0
    assert r["raw"] == 15

def test_ji_mixed():
    r = score_job_intelligence(["a", "b", "c", "d", "c"])
    # 0+1+2+3+2 = 8 -> 8*(100/15) = 53.3
    assert r["raw"] == 8
    assert abs(r["total"] - 53.3) < 0.1
    assert r["dimensions"][0]["desc"] == "Unit of work"
    assert r["dimensions"][0]["score_0_3"] == 0
    assert r["dimensions"][3]["score_0_3"] == 3  # item 4 = 'd'

def test_ji_case_insensitive():
    r = score_job_intelligence(["A", "B", "C", "D", "A"])
    assert r["raw"] == 0 + 1 + 2 + 3 + 0

def test_ji_wrong_length_raises():
    try:
        score_job_intelligence(["a", "b"])
        assert False, "should have raised"
    except ValueError:
        pass

def test_job_search_most_targeted():
    r = score_job_search("9-10", "more_than_5", "more_than_60", 1)
    assert r["total"] == 0.0

def test_job_search_most_untargeted():
    r = score_job_search("0", "none", "under_10", 5)
    assert r["total"] == 100.0

def test_job_search_excludes_not_yet_10():
    r = score_job_search("not_yet_10", "none", "under_10", 5)
    assert r["components"]["tailor"] is None
    # mean of tasks=100, time=100, scatter=100 -> 100
    assert r["total"] == 100.0

def test_scatter_mapping():
    for likert, expected in [(1, 0), (2, 25), (3, 50), (4, 75), (5, 100)]:
        r = score_job_search("0", "none", "under_10", likert)
        assert r["components"]["scatter"] == expected

def test_control_mean_and_shift():
    m1 = control_mean(3, 3, 3)
    m2 = control_mean(4, 4, 4)
    assert m1 == 3.0
    s = control_shift(m1, m2)
    assert s["delta"] == 1.0
    assert s["verdict"] == "discount"

    s2 = control_shift(3.0, 3.1)
    assert s2["verdict"] == "credible"

def test_quadrant_names():
    assert quadrant(80, 20) == "Volume Applicant"
    assert quadrant(80, 80) == "Busy Strategist"
    assert quadrant(20, 20) == "Drifting"
    assert quadrant(20, 80) == "Job Intelligent"
    assert quadrant(50, 50) == "Busy Strategist"  # midpoint inclusive both -> high/high


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
