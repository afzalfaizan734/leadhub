"""LeadHub - a small shared lead-management server for a dispatch team.

Layers, outermost first:
    api          HTTP routing and auth guards
    services     use cases (login, search, assign, report, sync, seed)
    repositories data access, one per table
    database     connection + schema + migrations
    fmcsa        gateway to FMCSA's public open-data API
    config       settings and domain vocabulary

Dependencies point inwards only: api -> services -> repositories -> database.
"""
__version__ = "2.0"
