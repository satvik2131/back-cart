/** Cents -> "$12.34". Display only; all values on the wire stay integer cents. */
export function money(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}
