package dev.relay.sandbox.orders.pricing;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;

class DiscountServiceTest {

    private final DiscountService discounts = new DiscountService();

    @Test
    void appliesKnownCodeCaseInsensitively() {
        assertThat(discounts.apply(" fall10 ", 10_000)).isEqualTo(9_000);
        assertThat(discounts.apply("VIP20", 10_000)).isEqualTo(8_000);
    }

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = {"   ", "NOPE"})
    void leavesSubtotalUnchangedWithoutAValidCode(String code) {
        assertThat(discounts.apply(code, 10_000)).isEqualTo(10_000);
    }
}
