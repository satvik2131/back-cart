import { useState } from "react";
import { getCart, removeCartItem, updateCartItem } from "../api/carts";
import type { Cart as CartT, CartItem, Order } from "../api/types";
import { useAction, useAsync } from "../hooks/useAsync";
import { money } from "../format";
import { CheckoutButton } from "./CheckoutButton";
import { ErrorBanner } from "./ErrorBanner";
import { LoadingSpinner } from "./LoadingSpinner";

interface Props {
  cartId: string;
  reloadSignal: number;
  couponCode: string;
  onCheckedOut: (order: Order) => void;
}

export function Cart({ cartId, reloadSignal, couponCode, onCheckedOut }: Props) {
  const cart = useAsync<CartT>(
    () => getCart(cartId),
    [cartId, reloadSignal],
  );

  return (
    <section className="panel">
      <div className="panel__head">
        <h2>Cart</h2>
        <span className="muted mono">{cartId}</span>
      </div>

      <ErrorBanner error={cart.error} onDismiss={cart.reload} />

      {cart.loading && !cart.data ? (
        <LoadingSpinner label="Loading cart…" />
      ) : null}

      {cart.data ? (
        <>
          {cart.data.status === "checked_out" ? (
            <p className="tag tag--done">This cart is checked out.</p>
          ) : null}

          {cart.data.items.length === 0 ? (
            <p className="muted">Cart is empty — add products above.</p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Product</th>
                  <th className="num">Unit</th>
                  <th className="num">Qty</th>
                  <th className="num">Subtotal</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {cart.data.items.map((item) => (
                  <CartLine
                    key={item.id}
                    cartId={cartId}
                    item={item}
                    editable={cart.data!.status === "open"}
                    onUpdated={cart.setData}
                  />
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={3} className="num strong">
                    Total
                  </td>
                  <td className="num strong">{money(cart.data.total_cents)}</td>
                  <td />
                </tr>
              </tfoot>
            </table>
          )}

          {cart.data.status === "open" && cart.data.items.length > 0 ? (
            <CheckoutButton
              cartId={cartId}
              couponCode={couponCode}
              onCheckedOut={onCheckedOut}
            />
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function CartLine({
  cartId,
  item,
  editable,
  onUpdated,
}: {
  cartId: string;
  item: CartItem;
  editable: boolean;
  onUpdated: (cart: CartT) => void;
}) {
  const [qty, setQty] = useState(item.quantity);
  const update = useAction(updateCartItem);
  const remove = useAction(removeCartItem);
  const busy = update.loading || remove.loading;

  async function onUpdate() {
    const result = await update.run(cartId, item.id, qty);
    if (result) onUpdated(result); // backend's fresh cart — no refetch needed
  }
  async function onRemove() {
    const result = await remove.run(cartId, item.id);
    if (result) onUpdated(result);
  }

  return (
    <>
      <tr>
        <td>{item.product_name}</td>
        <td className="num">{money(item.unit_price_cents)}</td>
        <td className="num">
          <input
            type="number"
            min={0}
            value={qty}
            onChange={(e) => setQty(Math.max(0, Number(e.target.value) || 0))}
            disabled={!editable || busy}
            aria-label={`Quantity of ${item.product_name}`}
          />
        </td>
        <td className="num">{money(item.line_subtotal_cents)}</td>
        <td>
          <div className="row-actions">
            <button
              type="button"
              className="btn btn--sm"
              onClick={onUpdate}
              disabled={!editable || busy || qty === item.quantity}
            >
              {update.loading ? (
                <LoadingSpinner inline label="…" />
              ) : qty === 0 ? (
                "Remove"
              ) : (
                "Update"
              )}
            </button>
            <button
              type="button"
              className="btn btn--sm btn--danger"
              onClick={onRemove}
              disabled={!editable || busy}
            >
              {remove.loading ? <LoadingSpinner inline label="…" /> : "×"}
            </button>
          </div>
        </td>
      </tr>
      {update.error || remove.error ? (
        <tr>
          <td colSpan={5}>
            <ErrorBanner
              error={update.error ?? remove.error}
              onDismiss={() => {
                update.clearError();
                remove.clearError();
              }}
            />
          </td>
        </tr>
      ) : null}
    </>
  );
}
