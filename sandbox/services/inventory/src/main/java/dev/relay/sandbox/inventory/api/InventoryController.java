package dev.relay.sandbox.inventory.api;

import java.util.List;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import dev.relay.sandbox.inventory.catalog.CatalogService;
import dev.relay.sandbox.inventory.catalog.ProductView;
import dev.relay.sandbox.inventory.stock.StockService;
import dev.relay.sandbox.inventory.stock.StockService.Reservation;

@RestController
@RequestMapping("/inventory")
class InventoryController {

    private final CatalogService catalog;
    private final StockService stock;

    InventoryController(CatalogService catalog, StockService stock) {
        this.catalog = catalog;
        this.stock = stock;
    }

    @GetMapping("/products")
    List<ProductView> products() {
        return catalog.topProducts();
    }

    @GetMapping("/products/{sku}")
    ProductView product(@PathVariable String sku) {
        return catalog.product(sku);
    }

    @PostMapping("/reserve")
    Reservation reserve(@Valid @RequestBody StockChange change) {
        return stock.reserve(change.sku(), change.quantity());
    }

    @PostMapping("/release")
    ResponseEntity<Void> release(@Valid @RequestBody StockChange change) {
        stock.release(change.sku(), change.quantity());
        return ResponseEntity.noContent().build();
    }

    record StockChange(@NotBlank String sku, @Min(1) @Max(100) int quantity) {
    }
}
