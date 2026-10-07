package dev.relay.sandbox.inventory.catalog;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cache.Cache;
import org.springframework.cache.annotation.CachingConfigurer;
import org.springframework.cache.annotation.EnableCaching;
import org.springframework.cache.interceptor.CacheErrorHandler;
import org.springframework.context.annotation.Configuration;

/**
 * Treats Redis as an optimisation, not a dependency: a cache failure is logged and
 * the request falls through to Postgres. The service stays up when Redis is down,
 * but every request pays for the uncached query.
 */
@Configuration
@EnableCaching
class CacheConfig implements CachingConfigurer {

    @Override
    public CacheErrorHandler errorHandler() {
        return new FallbackCacheErrorHandler();
    }

    static final class FallbackCacheErrorHandler implements CacheErrorHandler {

        private static final Logger log = LoggerFactory.getLogger(FallbackCacheErrorHandler.class);

        @Override
        public void handleCacheGetError(RuntimeException e, Cache cache, Object key) {
            log.error("Redis cache GET failed for {}::{}, falling back to database: {}",
                    cache.getName(), key, rootMessage(e));
        }

        @Override
        public void handleCachePutError(RuntimeException e, Cache cache, Object key, Object value) {
            log.warn("Redis cache PUT failed for {}::{}: {}", cache.getName(), key, rootMessage(e));
        }

        @Override
        public void handleCacheEvictError(RuntimeException e, Cache cache, Object key) {
            log.warn("Redis cache EVICT failed for {}::{}: {}", cache.getName(), key, rootMessage(e));
        }

        @Override
        public void handleCacheClearError(RuntimeException e, Cache cache) {
            log.warn("Redis cache CLEAR failed for {}: {}", cache.getName(), rootMessage(e));
        }

        private static String rootMessage(Throwable t) {
            Throwable root = t;
            while (root.getCause() != null && root.getCause() != root) {
                root = root.getCause();
            }
            return root.getClass().getSimpleName() + ": " + root.getMessage();
        }
    }
}
