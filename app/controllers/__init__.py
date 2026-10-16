"""Entry points: HTTP routes, the CLI, and the background workers (Pub/Sub, Redis streams).

Thin on purpose: parse the input, call a service, shape the output. No business rules here.
"""
