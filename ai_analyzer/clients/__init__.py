"""Clients for everything outside ai_analyzer: OpenAI, the main app, ToolSvc, Redis, the runs log file.

Services and the agent call these; they never build HTTP requests or publish to Redis themselves.
(Our own database is not a client: tables in ai_analyzer/models, queries in ai_analyzer/repositories.)
"""
