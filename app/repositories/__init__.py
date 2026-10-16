"""All SQL lives here: one module of query functions per table (models in app/models).

Every function takes the caller's session; repositories never commit. The service that called them
owns the transaction (session_scope()).
"""
