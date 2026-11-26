from datetime import date

import pytest

from signal_engine.data.massive import Massive
from signal_engine.macro import us_metals as U


def row(code, partner, t, usd):
    return {"cmdCode": code, "period": "202608", "flowCode": "M", "partnerCode": partner, "netWgt": t * 1000,
            "primaryValue": usd, "isNetWgtEstimated": False}


ROWS = [row("7403", 0, 10, 100), row("7403", 152, 10, 100), row("7601", 0, 5, 50), row("7601", 124, 5, 50)]


def test_imports():
    r = U.parse_imports({"data": ROWS}, {152: "Chile"}, "202608")
    assert r["copper"]["origins"]["Chile"]["t"] == 10 and "code 124" in r["aluminum"]["origins"]
    assert U.parse_imports({"data": []}, {}, "202609") is None


def test_imports_reject_gaps_and_duplicates():
    with pytest.raises(ValueError, match="miss"):
        U.parse_imports({"data": [*ROWS[:1], row("7403", 152, 5, 50), *ROWS[2:]]}, {}, "202608")
    with pytest.raises(ValueError, match="duplicate"):
        U.parse_imports({"data": [*ROWS, ROWS[1]]}, {}, "202608")


def cells(total=30):
    return [["COPPER"], ["Short Tons"], ["", "Activity Date: 11/24/2026"],
            ["DEPOSITORY", "PREV TOTAL", "TOTAL TODAY"], ["TOTAL REGISTERED", 9, "10"],
            ["TOTAL ELIGIBLE", 19, 20.0], ["TOTAL COPPER", 28, total]]


def test_comex():
    r = U.parse_comex(cells())
    assert (r["metal"], r["date"], r["registered"], r["total"]) == ("copper", "2026-11-24", 10, 30)


@pytest.mark.parametrize("bad", [cells(35), cells()[:-1], cells()[2:], [["GOLD"]]])
def test_comex_refuses_what_it_cannot_reconcile(bad):
    with pytest.raises(ValueError):
        U.parse_comex(bad)


def test_contract_months_and_all_in():
    c = U.contract_months("AUP", date(2026, 11, 25), 3)
    assert c == [("AUPX26.CMX", "2026-11"), ("AUPZ26.CMX", "2026-12"), ("AUPF27.CMX", "2027-01")]
    assert U.all_in(2000, 1) == pytest.approx(4204.62, abs=0.01)


class Resp:
    status_code = 200

    def __init__(self, d):
        self.d = d

    def json(self):
        return self.d


class Sess:
    def __init__(self, *replies):
        self.replies, self.urls = list(replies), []

    def get(self, url, **k):
        assert k["allow_redirects"] is False and "apiKey" not in str(k["params"])
        self.urls.append(url)
        return Resp(self.replies.pop(0))


def test_massive_pages_and_throttles():
    naps = []
    s = Sess({"results": [1], "next_url": "https://api.massive.com/v3/x?cursor=1"}, {"results": [2]})
    assert Massive("k", s, naps.append, lambda: 0.0).contracts("USO", "2026-12-24", 1, 2) == [1, 2]
    assert naps == [12.5]


def test_massive_never_sends_key_elsewhere():
    s = Sess({"results": [], "next_url": "https://api.massive.com.evil.test/x"})
    with pytest.raises(ValueError, match="refusing"):
        Massive("k", s, lambda _: None).contracts("USO", "2026-12-24", 1, 2)
    assert len(s.urls) == 1


def test_massive_needs_key(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        Massive()
