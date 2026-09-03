import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";

interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
}

function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  return new ApiError({
    code: "UNKNOWN",
    message: err instanceof Error ? err.message : "Unexpected error.",
    status: 0,
  });
}

/**
 * Runs `fn` immediately and whenever `deps` change. `loading` is true from the
 * instant the call fires, so callers can render a spinner with no gap. `reload`
 * re-runs it on demand (used after a mutation changes the data).
 */
export function useAsync<T>(
  fn: () => Promise<T>,
  deps: React.DependencyList,
): AsyncState<T> & {
  reload: () => void;
  /** Replace the data with a value already returned by the backend (e.g. the
   *  cart body a mutation responded with) — avoids a redundant refetch. */
  setData: (data: T) => void;
} {
  const [state, setState] = useState<AsyncState<T>>({
    data: null,
    loading: true,
    error: null,
  });
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const run = useCallback(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    fnRef
      .current()
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null });
      })
      .catch((err) => {
        if (!cancelled) {
          setState((s) => ({ ...s, loading: false, error: toApiError(err) }));
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(run, [run]);

  const reload = useCallback(() => {
    run();
  }, [run]);

  const setData = useCallback((data: T) => {
    setState({ data, loading: false, error: null });
  }, []);

  return { ...state, reload, setData };
}

/**
 * For button-triggered calls. `run` is a no-op while a call is already in
 * flight (belt-and-braces against double-clicks, on top of disabling the
 * button). Returns the resolved value so the caller can react to success.
 */
export function useAction<TArgs extends unknown[], T>(
  fn: (...args: TArgs) => Promise<T>,
): {
  run: (...args: TArgs) => Promise<T | undefined>;
  loading: boolean;
  error: ApiError | null;
  data: T | null;
  clearError: () => void;
} {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [data, setData] = useState<T | null>(null);
  const inFlight = useRef(false);

  const run = useCallback(
    async (...args: TArgs): Promise<T | undefined> => {
      if (inFlight.current) return undefined;
      inFlight.current = true;
      setLoading(true);
      setError(null);
      try {
        const result = await fn(...args);
        setData(result);
        return result;
      } catch (err) {
        setError(toApiError(err));
        return undefined;
      } finally {
        inFlight.current = false;
        setLoading(false);
      }
    },
    [fn],
  );

  const clearError = useCallback(() => setError(null), []);

  return { run, loading, error, data, clearError };
}
