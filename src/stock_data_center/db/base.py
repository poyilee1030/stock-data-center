"""The MetaData and column types every table declaration shares.

Schema v2 declares its tables on this MetaData; until Step 35-d removes them,
the v1 declarations in `metadata.py` add theirs to the same object, so the
migrations and the drift check see both generations.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)

aware_timestamp = postgresql.TIMESTAMP(timezone=True)
uuid_type = postgresql.UUID(as_uuid=True)
jsonb_type = postgresql.JSONB()
