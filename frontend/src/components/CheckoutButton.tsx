import { useState } from "react";
import { checkout } from "../api/checkout";
import type { Order } from "../api/types";
import { useAction } from "../hooks/useAsync";
import { ErrorBanner } from "./ErrorBanner";
import { LoadingSpinner } from "./LoadingSpinner";

interface Props {
  cartId: string;
  couponCode: string;
  onCheckedOut: (order: Order) => void;
}

function newKey(): string {
  return crypto.randomUUID();
}

/**
 * Demonstrates the backend's idempotency-key handling:
 *  - a fresh key is generated for each *new* checkout attempt;
 *  - if an attempt fails (especially a network error where the response was
 *    lost), the SAME key is kept, so clicking Checkout again is a genuine
 *    retry — the backend returns the original order instead of making a
 *    second one.
 * The current key and whether the next click is a retry are shown in the UI.
 */
export function CheckoutButton({ cartId, couponCode, onCheckedOut }: Props) {
  const [key, setKey] = useState(newKey);
  const [attempts, setAttempts] = useState(0);
  const action = useAction(checkout);

  const isRetry = attempts >= 1 && !action.loading;

  async function onClick() {
    setAttempts((n) => n + 1);
    const order = await action.run(cartId, key, couponCode.trim() || undefined);
    if (order) {
      onCheckedOut(order);
      setKey(newKey()); // next checkout is a new attempt
      setAttempts(0);
    }
  }

  return (
    <div className="checkout">
      <div className="checkout__keyline">
        <span className="muted">Idempotency-Key</span>
        <code className="mono">{key}</code>
        {attempts > 0 ? (
          <span className="muted">· attempt #{attempts}</span>
        ) : null}
      </div>

      <button
        type="button"
        className="btn btn--primary btn--lg"
        onClick={onClick}
        disabled={action.loading}
      >
        {action.loading ? (
          <LoadingSpinner inline label="Placing order…" />
        ) : isRetry ? (
          "Retry checkout (same key)"
        ) : (
          "Checkout"
        )}
      </button>

      {couponCode.trim() ? (
        <p className="muted">
          Applying coupon <code className="mono">{couponCode.trim()}</code>
        </p>
      ) : null}

      {action.error?.network ? (
        <div className="notice notice--warn" role="alert">
          No response received. The order may already have been placed. Click
          <strong> Retry checkout</strong> — it reuses key{" "}
          <code className="mono">{key}</code>, so you'll get the original order
          back if so, never a duplicate.
        </div>
      ) : (
        <ErrorBanner error={action.error} onDismiss={action.clearError} />
      )}

      {isRetry && !action.error?.network && action.error ? (
        <p className="muted">Retry will reuse the same key.</p>
      ) : null}
    </div>
  );
}
