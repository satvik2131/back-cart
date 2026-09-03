import { useState } from "react";
import { listProducts } from "../api/products";
import { addCartItem } from "../api/carts";
import type { Product } from "../api/types";
import { useAction, useAsync } from "../hooks/useAsync";
import { money } from "../format";
import { ErrorBanner } from "./ErrorBanner";
import { LoadingSpinner } from "./LoadingSpinner";

interface Props {
  cartId: string | null;
  /** Bumped by the parent after a checkout, so inventory is refetched. */
  reloadSignal: number;
  onItemAdded: () => void;
}

export function ProductList({ cartId, reloadSignal, onItemAdded }: Props) {
  const { data: products, loading, error, reload } = useAsync(
    listProducts,
    [reloadSignal],
  );

  return (
    <section className="panel">
      <div className="panel__head">
        <h2>Products</h2>
        <button
          type="button"
          className="btn btn--ghost"
          onClick={reload}
          disabled={loading}
        >
          {loading ? <LoadingSpinner inline label="Loading…" /> : "Refresh"}
        </button>
      </div>

      <ErrorBanner error={error} onDismiss={reload} />

      {loading && !products ? (
        <ul className="skeleton-list">
          {[0, 1, 2, 3].map((i) => (
            <li key={i} className="skeleton-row" />
          ))}
        </ul>
      ) : null}

      {products ? (
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th className="num">Price</th>
              <th className="num">Inventory</th>
              <th>Add to cart</th>
            </tr>
          </thead>
          <tbody>
            {products.map((p) => (
              <ProductRow
                key={p.id}
                product={p}
                cartId={cartId}
                onAdded={onItemAdded}
              />
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}

function ProductRow({
  product,
  cartId,
  onAdded,
}: {
  product: Product;
  cartId: string | null;
  onAdded: () => void;
}) {
  const [qty, setQty] = useState(1);
  const add = useAction(addCartItem);
  const outOfStock = product.inventory === 0;
  const overStock = qty > product.inventory;

  async function onAdd() {
    if (!cartId) return;
    const result = await add.run(cartId, product.id, qty);
    if (result) onAdded();
  }

  return (
    <>
      <tr>
        <td>{product.name}</td>
        <td className="num">{money(product.unit_price_cents)}</td>
        <td className="num">{product.inventory}</td>
        <td>
          <div className="add-controls">
            <input
              type="number"
              min={1}
              max={Math.max(product.inventory, 1)}
              value={qty}
              onChange={(e) =>
                setQty(Math.max(1, Number(e.target.value) || 1))
              }
              disabled={!cartId || outOfStock || add.loading}
              aria-label={`Quantity of ${product.name}`}
            />
            <button
              type="button"
              className="btn"
              onClick={onAdd}
              disabled={!cartId || outOfStock || overStock || add.loading}
              title={
                !cartId
                  ? "Create a cart first"
                  : outOfStock
                    ? "Out of stock"
                    : overStock
                      ? "More than the inventory shown"
                      : undefined
              }
            >
              {add.loading ? <LoadingSpinner inline label="Adding…" /> : "Add"}
            </button>
          </div>
        </td>
      </tr>
      {add.error ? (
        <tr>
          <td colSpan={4}>
            <ErrorBanner error={add.error} onDismiss={add.clearError} />
          </td>
        </tr>
      ) : null}
    </>
  );
}
