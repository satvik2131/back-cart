import { request } from "./client";
import type { Cart } from "./types";

export function createCart(): Promise<Cart> {
  return request<Cart>("/carts", { method: "POST" });
}

export function getCart(cartId: string): Promise<Cart> {
  return request<Cart>(`/carts/${cartId}`);
}

export function addCartItem(
  cartId: string,
  productId: string,
  quantity: number,
): Promise<Cart> {
  return request<Cart>(`/carts/${cartId}/items`, {
    method: "POST",
    body: { product_id: productId, quantity },
  });
}

export function updateCartItem(
  cartId: string,
  itemId: string,
  quantity: number,
): Promise<Cart> {
  return request<Cart>(`/carts/${cartId}/items/${itemId}`, {
    method: "PATCH",
    body: { quantity },
  });
}

export function removeCartItem(cartId: string, itemId: string): Promise<Cart> {
  return request<Cart>(`/carts/${cartId}/items/${itemId}`, { method: "DELETE" });
}
