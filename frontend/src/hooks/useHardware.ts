import { useEffect, useState, useCallback } from "react";
import { HardwareTelemetry } from "../types";
import { fetchHardware } from "../services/api";

export function useHardware(pollIntervalMs = 2000) {
  const [telemetry, setTelemetry] = useState<HardwareTelemetry | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchHardware();
      setTelemetry(data);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Hardware telemetry error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, pollIntervalMs);
    return () => clearInterval(interval);
  }, [refresh, pollIntervalMs]);

  return { telemetry, loading, error, refresh };
}

