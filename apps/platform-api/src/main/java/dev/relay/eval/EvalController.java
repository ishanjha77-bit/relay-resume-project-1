package dev.relay.eval;

import java.util.List;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;

import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** Scorecard data: written by evals/runner.py, charted by the console's evals page. */
@RestController
@RequestMapping("/api/evals")
@Validated
class EvalController {

    private final EvalRepository evals;

    EvalController(EvalRepository evals) {
        this.evals = evals;
    }

    @PostMapping("/runs")
    EvalRun record(@Valid @RequestBody EvalRun run) {
        return evals.upsert(run);
    }

    /** Newest first; one batch, or all of them. */
    @GetMapping("/runs")
    List<EvalRun> runs(@RequestParam(required = false) String batch,
            @RequestParam(defaultValue = "200") @Min(1) @Max(1000) int limit) {
        return evals.list(batch, limit);
    }

    /** One scorecard per batch, newest first. */
    @GetMapping("/batches")
    List<EvalBatch> batches(@RequestParam(defaultValue = "50") @Min(1) @Max(500) int limit) {
        return evals.batches(limit);
    }
}
