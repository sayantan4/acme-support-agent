"""Business rules: what C1 and C3 do with each event. Called by the controllers (stream workers).

The agent itself (ai_analyzer/agent) is part of this layer. Services reach the outside world only
through ai_analyzer/clients, and our own database through ai_analyzer/repositories.
"""
