package dev.relay.incident;

import java.util.Locale;

public enum Severity {
    critical,
    warning,
    info;

    /** Unknown or missing severities count as warnings. */
    public static Severity parse(String value) {
        if (value == null) {
            return warning;
        }
        try {
            return valueOf(value.trim().toLowerCase(Locale.ROOT));
        } catch (IllegalArgumentException e) {
            return warning;
        }
    }

    public boolean moreSevereThan(Severity other) {
        return ordinal() < other.ordinal();
    }
}
