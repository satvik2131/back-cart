interface Props {
  /** Optional label shown next to the spinner. */
  label?: string;
  inline?: boolean;
}

/** The single loading indicator used everywhere a request is in flight. */
export function LoadingSpinner({ label, inline = false }: Props) {
  return (
    <span
      className={inline ? "spinner spinner--inline" : "spinner"}
      role="status"
      aria-live="polite"
    >
      <span className="spinner__dot" aria-hidden="true" />
      {label ? <span className="spinner__label">{label}</span> : null}
      <span className="sr-only">Loading</span>
    </span>
  );
}
