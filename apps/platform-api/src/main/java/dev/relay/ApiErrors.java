package dev.relay;

import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.mvc.method.annotation.ResponseEntityExceptionHandler;

import tools.jackson.core.JacksonException;

/** Client mistakes become 400 problem details instead of 500s. */
@RestControllerAdvice
class ApiErrors extends ResponseEntityExceptionHandler {

    @ExceptionHandler(IllegalArgumentException.class)
    ProblemDetail badRequest(IllegalArgumentException e) {
        return ProblemDetail.forStatusAndDetail(HttpStatus.BAD_REQUEST, e.getMessage());
    }

    @ExceptionHandler(JacksonException.class)
    ProblemDetail malformedJson(JacksonException e) {
        return ProblemDetail.forStatusAndDetail(HttpStatus.BAD_REQUEST, "malformed JSON: " + e.getOriginalMessage());
    }
}
