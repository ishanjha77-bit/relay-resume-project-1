package dev.relay.sandbox.orders.clients;

import java.net.SocketTimeoutException;
import java.net.http.HttpConnectTimeoutException;
import java.net.http.HttpTimeoutException;
import java.time.Duration;

/** A call to another service failed or timed out. */
public class DependencyException extends RuntimeException {

    private final String dependency;
    private final boolean timeout;

    public DependencyException(String dependency, String operation, Duration readTimeout, Throwable cause) {
        super(describe(dependency, operation, readTimeout, cause), cause);
        this.dependency = dependency;
        this.timeout = find(cause, HttpTimeoutException.class) != null || find(cause, SocketTimeoutException.class) != null;
    }

    public String dependency() {
        return dependency;
    }

    public boolean timeout() {
        return timeout;
    }

    // The JDK client reports a read timeout as HttpTimeoutException("get"), so
    // spell timeouts out instead of echoing the cause's message.
    private static String describe(String dependency, String operation, Duration readTimeout, Throwable cause) {
        String call = dependency + " " + operation;
        if (find(cause, HttpConnectTimeoutException.class) != null) {
            return call + " failed: could not connect to " + dependency + " (connect timed out)";
        }
        if (find(cause, HttpTimeoutException.class) != null || find(cause, SocketTimeoutException.class) != null) {
            return call + " timed out: no response within " + readTimeout.toMillis() + " ms";
        }
        return call + " failed: " + cause.getMessage();
    }

    private static <T extends Throwable> T find(Throwable t, Class<T> type) {
        for (Throwable c = t; c != null; c = c.getCause()) {
            if (type.isInstance(c)) {
                return type.cast(c);
            }
        }
        return null;
    }
}
