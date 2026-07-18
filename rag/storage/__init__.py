"""Storage adapters retained by the V3 service.

The active adapters are ``milvus_client`` and ``milvus_writer`` for product
evidence vectors. The old PostgreSQL/parent-chunk/cache stack was removed
because no current V3 request or ingestion script called it.
"""
