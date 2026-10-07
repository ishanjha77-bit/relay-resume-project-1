package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"

	"relay.dev/sandbox/golib/httpx"
)

type chargeRequest struct {
	OrderID     string `json:"orderId"`
	AmountCents int64  `json:"amountCents"`
	Currency    string `json:"currency"`
	CustomerID  string `json:"customerId"`
}

type chargeResponse struct {
	PaymentID string `json:"paymentId,omitempty"`
	Status    string `json:"status"`
	Reason    string `json:"reason,omitempty"`
}

type errorResponse struct {
	Error string `json:"error"`
}

var supportedCurrencies = map[string]bool{"USD": true, "EUR": true, "INR": true}

type service struct {
	psp    *pspClient
	faults *faultRunner
	logger *slog.Logger
}

func (s *service) charge(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var req chargeRequest
	if err := json.NewDecoder(io.LimitReader(r.Body, 64<<10)).Decode(&req); err != nil ||
		req.OrderID == "" || req.AmountCents <= 0 || !supportedCurrencies[req.Currency] {
		httpx.WriteJSON(w, http.StatusBadRequest, errorResponse{"invalid_request"})
		return
	}
	s.faults.onCharge()

	result, err := s.psp.chargeWithRetry(ctx, req)
	switch {
	case errors.Is(err, errRateLimited):
		s.logger.ErrorContext(ctx, "charge failed: psp rate limit persisted after retry",
			"order_id", req.OrderID, "amount_cents", req.AmountCents)
		httpx.WriteJSON(w, http.StatusServiceUnavailable, errorResponse{"psp_rate_limited"})
	case errors.Is(err, errPSPUnavailable):
		s.logger.ErrorContext(ctx, "charge failed: psp unavailable after retry",
			"order_id", req.OrderID, "amount_cents", req.AmountCents)
		httpx.WriteJSON(w, http.StatusBadGateway, errorResponse{"psp_unavailable"})
	case errors.Is(err, context.Canceled), errors.Is(err, context.DeadlineExceeded):
		// The caller gave up; nobody reads this response, but metrics still count it.
		httpx.WriteJSON(w, http.StatusGatewayTimeout, errorResponse{"caller_cancelled"})
	case err != nil:
		s.logger.ErrorContext(ctx, "charge failed", "order_id", req.OrderID, "error", err)
		httpx.WriteJSON(w, http.StatusInternalServerError, errorResponse{"internal_error"})
	case result.declined:
		httpx.WriteJSON(w, http.StatusPaymentRequired, chargeResponse{Status: "declined", Reason: "insufficient_funds"})
	default:
		httpx.WriteJSON(w, http.StatusOK, chargeResponse{PaymentID: result.paymentID, Status: "captured"})
	}
}
