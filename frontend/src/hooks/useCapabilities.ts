import { useCallback, useEffect, useState } from "react";

import { getCapabilities, toApiError } from "../api/client";
import { ApiError, type Capabilities } from "../api/types";

export type AsyncView<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "success"; data: T }
  | { status: "error"; error: ApiError };

export function useCapabilities(): AsyncView<Capabilities> {
  const [view, setView] = useState<AsyncView<Capabilities>>({ status: "idle" });

  const load = useCallback(async () => {
    setView({ status: "loading" });
    try {
      const data = await getCapabilities();
      setView({ status: "success", data });
    } catch (error) {
      setView({ status: "error", error: toApiError(error) });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return view;
}
