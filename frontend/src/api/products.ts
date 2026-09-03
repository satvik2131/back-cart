import { request } from "./client";
import type { Product } from "./types";

export function listProducts(): Promise<Product[]> {
  return request<Product[]>("/products");
}
