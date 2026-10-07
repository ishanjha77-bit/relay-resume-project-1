package dev.relay.sandbox.orders.pricing;

import java.util.Locale;
import java.util.Map;

import org.springframework.stereotype.Service;

@Service
public class DiscountService {

    private static final Map<String, Integer> PERCENT_OFF = Map.of(
            "FALL10", 10,
            "VIP20", 20);

    /** Applies a discount code to a subtotal. Missing or unknown codes leave it unchanged. */
    public long apply(String code, long subtotalCents) {
        if (code == null || code.isBlank()) {
            return subtotalCents;
        }
        Integer percent = PERCENT_OFF.get(code.trim().toUpperCase(Locale.ROOT));
        if (percent == null) {
            return subtotalCents;
        }
        return subtotalCents - subtotalCents * percent / 100;
    }
}
