import type { Order } from "../api/types";
import { money } from "../format";

/** Shows a placed order exactly as the backend snapshotted it. */
export function OrderReceipt({ order }: { order: Order }) {
  return (
    <div className="receipt" role="status">
      <div className="receipt__head">
        <h3>Order placed</h3>
        <code className="mono">{order.id}</code>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>Product</th>
            <th className="num">Unit</th>
            <th className="num">Qty</th>
            <th className="num">Line total</th>
          </tr>
        </thead>
        <tbody>
          {order.items.map((item) => (
            <tr key={item.id}>
              <td>{item.product_name}</td>
              <td className="num">{money(item.unit_price_cents)}</td>
              <td className="num">{item.quantity}</td>
              <td className="num">{money(item.line_total_cents)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <dl className="receipt__totals">
        <div>
          <dt>Gross</dt>
          <dd>{money(order.gross_total_cents)}</dd>
        </div>
        <div>
          <dt>Discount</dt>
          <dd>−{money(order.discount_cents)}</dd>
        </div>
        <div className="strong">
          <dt>Net paid</dt>
          <dd>{money(order.net_total_cents)}</dd>
        </div>
      </dl>
    </div>
  );
}
