"""Vulture whitelist for intentional false positives.

Protocol method stubs (PEP 544) declare interface parameters that are never
used in the stub body but are part of the contract and cannot be renamed
(keyword-only names must match implementations and call sites).
"""

# BattingOrderProvider.complete — apps/api/app/lineup_model/batting_order/types.py
system  # noqa
user  # noqa
schema  # noqa
