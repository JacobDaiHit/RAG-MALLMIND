"""HTTP boundary package for the V3 recommendation service.

``recommendation_app`` constructs the FastAPI application; ``routes.chat`` is
the chat/SSE entry point, while products and feedback expose small auxiliary
APIs. Importing this package alone must not connect to models or databases.
"""
