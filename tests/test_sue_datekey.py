"""SUE-`filed` PIT contract test — scaffold.

Activated once factors/pead.py exists. The contract:

  Given a SUE value computed from EPS at fiscal period_end P, that SUE may
  appear in the engine ONLY on dates >= the original 10-Q/10-K `filed` date
  for period P. Specifically:

    - No SUE row may have a `signal_date` strictly less than the `filed`
      date of the original (non-amendment) EPS report it was computed from.

If this passes silently the PIT plumbing has a hole in the most subtle place
in the engine.
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="activated when signal_engine/factors/pead.py exists (build step 3)")
def test_sue_never_attached_before_its_filed_date() -> None:
    # Implementation pattern, once pead.compute_sue exists:
    #
    #   from signal_engine.factors.pead import compute_sue
    #   sue_df = compute_sue(con, as_of=...)
    #   filings = con.execute('''
    #       SELECT cik, period_end, MIN(filed) AS first_filed
    #       FROM edgar_facts
    #       WHERE concept = 'eps_diluted' AND is_amendment = FALSE
    #       GROUP BY cik, period_end
    #   ''').pl()
    #   joined = sue_df.join(filings, on=['cik', 'period_end'])
    #   violations = joined.filter(pl.col('signal_date') < pl.col('first_filed'))
    #   assert violations.height == 0, violations
    raise NotImplementedError
