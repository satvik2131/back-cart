import { request } from "./client";
import type { Order } from "./types";

export function checkout(
  cartId: string,
  idempotencyKey: string,
  couponCode?: string,
): Promise<Order> {
  return request<Order>(`/carts/${cartId}/checkout`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: couponCode ? { coupon_code: couponCode } : {},
  });
}
