import { useEffect, useState } from "react";

export type Async<T> = { status: "loading" } | { status: "error"; error: Error } | { status: "done"; value: T };

/** Runs `load` whenever `deps` change; a stale answer never overwrites a newer one. */
export function useAsync<T>(load: ((signal: AbortSignal) => Promise<T>) | null, deps: unknown[]): Async<T> {
  const [state, setState] = useState<Async<T>>({ status: "loading" });
  useEffect(() => {
    if (load === null) return;
    const controller = new AbortController();
    setState({ status: "loading" });
    load(controller.signal).then(
      (value) => controller.signal.aborted || setState({ status: "done", value }),
      (error: Error) => controller.signal.aborted || setState({ status: "error", error }),
    );
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}
