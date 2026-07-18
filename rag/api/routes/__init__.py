"""Namespace marker for independently registered FastAPI route modules.

It intentionally contains no router and no side effect. The application
explicitly imports ``routes.chat`` and ``routes.feedback`` in
``recommendation_app.py``; this file merely makes ``routes`` a Python package.
"""
