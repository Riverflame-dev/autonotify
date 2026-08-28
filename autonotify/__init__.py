"""Autoupdate — a privacy-preserving Gmail watcher for job-application activity.

Three stages, cost increasing at each:
  0. Pull (free)      — Gmail metadata since last successful run.
  1. Classify (free)  — local embeddings + a logistic head → applied / update / ignore.
  2. Verify (paid)    — Claude confirms the few update candidates, one notification per company.
"""

__version__ = "0.1.0"
