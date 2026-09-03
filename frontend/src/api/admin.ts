import { request } from "./client";
import type { AdminReport, Coupon } from "./types";

export function generateCoupon(): Promise<Coupon> {
  return request<Coupon>("/admin/coupons/generate", { method: "POST" });
}

export function getReport(): Promise<AdminReport> {
  return request<AdminReport>("/admin/report");
}
