"""Discovery sources for a domain's disclosure/bounty policy page.

Each module exposes an async function that returns either None or a dict
with at least a `url`/`policy_url` field plus enough context to classify it.
"""
