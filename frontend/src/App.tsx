import { useReducer, useState } from "react";
import { createCart } from "./api/carts";
import type { Order } from "./api/types";
import { useAction } from "./hooks/useAsync";
import { AdminPanel } from "./components/AdminPanel";
import { Cart } from "./components/Cart";
import { ErrorBanner } from "./components/ErrorBanner";
import { LoadingSpinner } from "./components/LoadingSpinner";
import { OrderReceipt } from "./components/OrderReceipt";
import { ProductList } from "./components/ProductList";

const increment = (n: number) => n + 1;

export default function App() {
  const [cartId, setCartId] = useState<string | null>(null);
  const [couponCode, setCouponCode] = useState("");
  const [lastOrder, setLastOrder] = useState<Order | null>(null);

  // Bumped to trigger a targeted refetch, only when an action changed the data.
  const [productsSignal, refetchProducts] = useReducer(increment, 0);
  const [cartSignal, refetchCart] = useReducer(increment, 0);

  const create = useAction(createCart);

  async function onCreateCart() {
    const cart = await create.run();
    if (cart) {
      setCartId(cart.id);
      setLastOrder(null);
    }
  }

  function onCheckedOut(order: Order) {
    setLastOrder(order);
    refetchCart(); // cart is now checked_out
    refetchProducts(); // inventory changed
  }

  return (
    <div className="app">
      <header className="app__header">
        <h1>back-cart</h1>
        <p className="muted">
          A thin UI over the backend — real requests, real latency, real errors.
        </p>
      </header>

      <main className="app__grid">
        <ProductList
          cartId={cartId}
          reloadSignal={productsSignal}
          onItemAdded={refetchCart}
        />

        <section className="stack">
          <div className="panel">
            <div className="panel__head">
              <h2>Your cart</h2>
              <button
                type="button"
                className="btn btn--primary"
                onClick={onCreateCart}
                disabled={create.loading}
              >
                {create.loading ? (
                  <LoadingSpinner inline label="Creating…" />
                ) : cartId ? (
                  "Create new cart"
                ) : (
                  "Create cart"
                )}
              </button>
            </div>
            <ErrorBanner error={create.error} onDismiss={create.clearError} />
            {!cartId ? (
              <p className="muted">Create a cart to start adding products.</p>
            ) : null}
          </div>

          {cartId ? (
            <Cart
              cartId={cartId}
              reloadSignal={cartSignal}
              couponCode={couponCode}
              onCheckedOut={onCheckedOut}
            />
          ) : null}

          {lastOrder ? <OrderReceipt order={lastOrder} /> : null}
        </section>

        <AdminPanel
          couponCode={couponCode}
          onCouponCodeChange={setCouponCode}
        />
      </main>
    </div>
  );
}
