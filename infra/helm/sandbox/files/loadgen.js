// Steady synthetic traffic for the sandbox shop. Arrival-rate executors keep
// the request rate constant even when the system slows down, so a degraded
// service sees real backpressure (more in-flight requests), like production.
import http from 'k6/http';

const BASE = __ENV.TARGET || 'http://gateway:8080';
const HOT_SKUS = 20;   // 80% of traffic goes to the first 20 SKUs
const ALL_SKUS = 200;
const FOREVER = '8760h';

export const options = {
  discardResponseBodies: true,
  scenarios: {
    browse: {
      executor: 'constant-arrival-rate', exec: 'browse',
      rate: Number(__ENV.BROWSE_RATE || 8), timeUnit: '1s', duration: FOREVER,
      preAllocatedVUs: 10, maxVUs: 100,
    },
    checkout: {
      executor: 'constant-arrival-rate', exec: 'checkout',
      rate: Number(__ENV.CHECKOUT_RATE || 4), timeUnit: '1s', duration: FOREVER,
      preAllocatedVUs: 10, maxVUs: 100,
    },
    myOrders: {
      executor: 'constant-arrival-rate', exec: 'myOrders',
      rate: Number(__ENV.MY_ORDERS_RATE || 2), timeUnit: '1s', duration: FOREVER,
      preAllocatedVUs: 5, maxVUs: 50,
    },
  },
};

const PARAMS = { timeout: '10s' };
const JSON_PARAMS = { timeout: '10s', headers: { 'Content-Type': 'application/json' } };

function pick(n) {
  return 1 + Math.floor(Math.random() * n);
}

function sku() {
  const n = Math.random() < 0.8 ? pick(HOT_SKUS) : pick(ALL_SKUS);
  return 'SKU-' + String(n).padStart(4, '0');
}

function customer() {
  return 'c-' + pick(5000);
}

export function browse() {
  if (Math.random() < 0.5) {
    http.get(`${BASE}/api/products`, PARAMS);
  } else {
    http.get(`${BASE}/api/products/${sku()}`, PARAMS);
  }
}

export function checkout() {
  const order = { customerId: customer(), sku: sku(), quantity: pick(3) };
  if (Math.random() < 0.3) {
    order.discountCode = Math.random() < 0.5 ? 'FALL10' : 'VIP20';
  }
  http.post(`${BASE}/api/orders`, JSON.stringify(order), JSON_PARAMS);
}

export function myOrders() {
  http.get(`${BASE}/api/orders?customerId=${customer()}&limit=10`, PARAMS);
}
