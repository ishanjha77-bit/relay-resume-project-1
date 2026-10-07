-- Six months of order history so the table is big enough for query plans to
-- matter: with the customer index a lookup takes ~1 ms, without it Postgres
-- scans ~2M rows.
INSERT INTO orders (id, customer_id, sku, quantity, total_cents, status, payment_id, created_at)
SELECT gen_random_uuid(),
       'c-' || (1 + (random() * 4999)::int),
       'SKU-' || lpad((1 + (random() * 199)::int)::text, 4, '0'),
       1 + (random() * 4)::int,
       (500 + random() * 20000)::bigint,
       'CONFIRMED',
       'pay_' || substr(md5(g::text), 1, 16),
       now() - random() * interval '180 days'
FROM generate_series(1, 2000000) AS g;

ANALYZE orders;
