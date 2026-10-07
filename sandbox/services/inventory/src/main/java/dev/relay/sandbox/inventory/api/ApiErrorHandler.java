package dev.relay.sandbox.inventory.api;

import jakarta.servlet.http.HttpServletRequest;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.mvc.method.annotation.ResponseEntityExceptionHandler;

import dev.relay.sandbox.inventory.stock.OutOfStockException;
import dev.relay.sandbox.inventory.stock.UnknownSkuException;

@RestControllerAdvice
class ApiErrorHandler extends ResponseEntityExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(ApiErrorHandler.class);

    @ExceptionHandler(OutOfStockException.class)
    ProblemDetail outOfStock(OutOfStockException e) {
        return ProblemDetail.forStatusAndDetail(HttpStatus.CONFLICT, e.getMessage());
    }

    @ExceptionHandler(UnknownSkuException.class)
    ProblemDetail unknownSku(UnknownSkuException e) {
        return ProblemDetail.forStatusAndDetail(HttpStatus.NOT_FOUND, e.getMessage());
    }

    @ExceptionHandler(DataAccessException.class)
    ProblemDetail database(DataAccessException e, HttpServletRequest request) {
        log.error("{} {} failed: database error", request.getMethod(), request.getRequestURI(), e);
        return ProblemDetail.forStatusAndDetail(HttpStatus.INTERNAL_SERVER_ERROR, "database error");
    }

    @ExceptionHandler(Exception.class)
    ProblemDetail unexpected(Exception e, HttpServletRequest request) {
        log.error("{} {} failed: unhandled exception", request.getMethod(), request.getRequestURI(), e);
        return ProblemDetail.forStatusAndDetail(HttpStatus.INTERNAL_SERVER_ERROR, "internal error");
    }
}
