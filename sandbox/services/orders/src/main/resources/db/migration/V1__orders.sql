CREATE TABLE orders (
    id            uuid        PRIMARY KEY,
    customer_id   text        NOT NULL,
    sku           text        NOT NULL,
    quantity      int         NOT NULL CHECK (quantity > 0),
    total_cents   bigint      NOT NULL CHECK (total_cents >= 0),
    status        text        NOT NULL,
    payment_id    text,
    discount_code text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- Serves GET /orders?customerId=… ("my orders"). Dropping it turns that query
-- into a sequential scan over the whole table.
CREATE INDEX orders_customer_created_idx ON orders (customer_id, created_at DESC);
CREATE INDEX orders_created_idx ON orders (created_at DESC);
