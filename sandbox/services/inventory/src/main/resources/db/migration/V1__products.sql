CREATE TABLE products (
    sku         text        PRIMARY KEY,
    name        text        NOT NULL,
    category    text        NOT NULL,
    price_cents bigint      NOT NULL CHECK (price_cents > 0),
    stock       int         NOT NULL CHECK (stock >= 0),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE stock_movements (
    id         bigserial   PRIMARY KEY,
    sku        text        NOT NULL REFERENCES products (sku),
    delta      int         NOT NULL,
    reason     text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX stock_movements_sku_created_idx ON stock_movements (sku, created_at DESC);

INSERT INTO products (sku, name, category, price_cents, stock)
SELECT 'SKU-' || lpad(g::text, 4, '0'),
       (ARRAY['Trail', 'Summit', 'Harbor', 'Canyon', 'Aurora', 'Ember', 'Tidal', 'Granite'])[1 + g % 8]
           || ' ' ||
       (ARRAY['Backpack', 'Jacket', 'Bottle', 'Lantern', 'Tent', 'Boots', 'Stove', 'Hammock'])[1 + (g / 8) % 8],
       (ARRAY['bags', 'apparel', 'kitchen', 'lighting', 'shelter', 'footwear'])[1 + g % 6],
       (900 + (g * 7919) % 25000)::bigint,
       1000
FROM generate_series(1, 200) AS g;
