"""Moved to `stock_data_center.ingestion.ixbrl` (Step 35-d-1).

The v1 callers still import it from here until 35-d-2 removes them; they get
the same module object, private names included.
"""

import sys

from stock_data_center.ingestion import ixbrl as _ixbrl

sys.modules[__name__] = _ixbrl
