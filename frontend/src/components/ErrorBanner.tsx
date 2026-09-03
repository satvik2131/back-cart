import type { ApiError } from "../api/client";

interface Props {
  error: ApiError | null;
  onDismiss?: () => void;
}

/**
 * Shows the backend's actual error code + message. Never a generic
 * "something went wrong". `details` is rendered when present because the
 * backend puts useful context there (e.g. requested vs available inventory).
 */
export function ErrorBanner({ error, onDismiss }: Props) {
  if (!error) return null;
  const hasDetails = Object.keys(error.details).length > 0;
  return (
    <div className="error-banner" role="alert">
      <div className="error-banner__row">
        <code className="error-banner__code">{error.code}</code>
        <span className="error-banner__message">{error.message}</span>
        {onDismiss ? (
          <button
            type="button"
            className="error-banner__dismiss"
            onClick={onDismiss}
            aria-label="Dismiss error"
          >
            ×
          </button>
        ) : null}
      </div>
      {hasDetails ? (
        <pre className="error-banner__details">
          {JSON.stringify(error.details, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}
