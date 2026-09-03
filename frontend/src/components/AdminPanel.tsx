import { generateCoupon, getReport } from "../api/admin";
import { useAction } from "../hooks/useAsync";
import { money } from "../format";
import { ErrorBanner } from "./ErrorBanner";
import { LoadingSpinner } from "./LoadingSpinner";

interface Props {
  couponCode: string;
  onCouponCodeChange: (value: string) => void;
}

export function AdminPanel({ couponCode, onCouponCodeChange }: Props) {
  const generate = useAction(generateCoupon);
  const report = useAction(getReport);

  return (
    <section className="panel panel--admin">
      <div className="panel__head">
        <h2>Admin</h2>
        <span className="tag tag--admin">unauthenticated · administrative</span>
      </div>

      <div className="admin-block">
        <h3>Coupons</h3>
        <button
          type="button"
          className="btn"
          onClick={() => generate.run()}
          disabled={generate.loading}
        >
          {generate.loading ? (
            <LoadingSpinner inline label="Generating…" />
          ) : (
            "Generate coupon"
          )}
        </button>

        {generate.data ? (
          <p className="notice notice--ok">
            Generated <code className="mono">{generate.data.code}</code> —{" "}
            {generate.data.discount_percent}% off, milestone{" "}
            {generate.data.milestone_number}.{" "}
            <button
              type="button"
              className="btn btn--sm btn--ghost"
              onClick={() => onCouponCodeChange(generate.data!.code)}
            >
              Use at checkout
            </button>
          </p>
        ) : null}
        <ErrorBanner error={generate.error} onDismiss={generate.clearError} />

        <label className="field">
          <span>Coupon code for checkout</span>
          <input
            type="text"
            value={couponCode}
            placeholder="e.g. SAVE10-XXXXXXXX"
            onChange={(e) => onCouponCodeChange(e.target.value)}
          />
        </label>
      </div>

      <div className="admin-block">
        <h3>Report</h3>
        <button
          type="button"
          className="btn"
          onClick={() => report.run()}
          disabled={report.loading}
        >
          {report.loading ? (
            <LoadingSpinner inline label="Loading…" />
          ) : report.data ? (
            "Refresh report"
          ) : (
            "View report"
          )}
        </button>
        <ErrorBanner error={report.error} onDismiss={report.clearError} />

        {report.data ? (
          <div className="report">
            <dl className="report__grid">
              <div>
                <dt>Successful orders</dt>
                <dd>{report.data.total_successful_orders}</dd>
              </div>
              <div>
                <dt>Gross revenue</dt>
                <dd>{money(report.data.gross_revenue_cents)}</dd>
              </div>
              <div>
                <dt>Total discounts</dt>
                <dd>{money(report.data.total_discount_cents)}</dd>
              </div>
              <div>
                <dt>Net revenue</dt>
                <dd>{money(report.data.net_revenue_cents)}</dd>
              </div>
              <div>
                <dt>Coupons generated</dt>
                <dd>{report.data.coupons_generated}</dd>
              </div>
              <div>
                <dt>Coupons available</dt>
                <dd>{report.data.coupons_available}</dd>
              </div>
              <div>
                <dt>Coupons redeemed</dt>
                <dd>{report.data.coupons_redeemed}</dd>
              </div>
            </dl>

            <h4>Quantity sold by product</h4>
            {report.data.quantity_sold_by_product.length === 0 ? (
              <p className="muted">Nothing sold yet.</p>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Product</th>
                    <th className="num">Units</th>
                  </tr>
                </thead>
                <tbody>
                  {report.data.quantity_sold_by_product.map((row) => (
                    <tr key={row.product_id}>
                      <td>{row.product_name}</td>
                      <td className="num">{row.quantity_sold}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        ) : null}
      </div>
    </section>
  );
}
