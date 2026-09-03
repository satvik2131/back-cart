// Response/request shapes mirroring the backend Pydantic schemas.
// All money is an integer count of cents.

export interface Product {
  id: string;
  name: string;
  unit_price_cents: number;
  inventory: number;
  created_at: string;
  updated_at: string;
}

export interface CartItem {
  id: string;
  product_id: string;
  product_name: string;
  unit_price_cents: number;
  quantity: number;
  line_subtotal_cents: number;
}

export interface Cart {
  id: string;
  status: "open" | "checked_out";
  items: CartItem[];
  total_cents: number;
  created_at: string;
  updated_at: string;
}

export interface OrderItem {
  id: string;
  product_id: string;
  product_name: string;
  unit_price_cents: number;
  quantity: number;
  line_total_cents: number;
}

export interface Order {
  id: string;
  cart_id: string;
  status: "success";
  items: OrderItem[];
  gross_total_cents: number;
  discount_cents: number;
  net_total_cents: number;
  created_at: string;
}

export interface Coupon {
  id: string;
  code: string;
  discount_percent: number;
  milestone_number: number;
  status: "available" | "redeemed";
  created_at: string;
  redeemed_at: string | null;
  redeemed_by_order_id: string | null;
}

export interface ProductSales {
  product_id: string;
  product_name: string;
  quantity_sold: number;
}

export interface AdminReport {
  total_successful_orders: number;
  gross_revenue_cents: number;
  total_discount_cents: number;
  net_revenue_cents: number;
  coupons_generated: number;
  coupons_available: number;
  coupons_redeemed: number;
  quantity_sold_by_product: ProductSales[];
}

/** The backend's structured error envelope: {"error": {code, message, details}}. */
export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
  };
}
