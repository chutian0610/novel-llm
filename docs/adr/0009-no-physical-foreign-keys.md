# Prefer ID references over physical foreign keys

The schema does not create PostgreSQL foreign-key constraints between domain tables. References remain strongly typed UUID columns with indexes, and domain services validate referenced objects in the same transaction before creating or archiving data. This keeps physical cleanup and maintenance scripts less constrained by database-level dependency edges.

The trade-off is accepted explicitly: direct SQL maintenance can create orphan references, and application-level checks are the primary integrity boundary. The product API provides no hard delete for business data in the first version; archive and supersede remain the normal lifecycle operations.
