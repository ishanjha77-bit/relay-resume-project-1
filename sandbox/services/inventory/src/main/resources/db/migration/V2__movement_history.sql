-- A week of movement history. The catalog query aggregates it, which is cheap
-- behind the Redis cache and expensive without it.
INSERT INTO stock_movements (sku, delta, reason, created_at)
SELECT 'SKU-' || lpad((1 + (random() * 199)::int)::text, 4, '0'),
       -(1 + (random() * 4)::int),
       'reserve',
       now() - random() * interval '7 days'
FROM generate_series(1, 600000);

ANALYZE stock_movements;
