"use client";

import { useEffect, useMemo, useState } from "react";

import { LoadingIndicator } from "./ui/LoadingIndicator";

type Model = {
  id: string;
  name: string;
  context_length: number | null;
  pricing: Record<string, string>;
};

function price(value: string | undefined, multiplier = 1) {
  if (value === undefined || !Number.isFinite(Number(value)) || Number(value) < 0) return "Not listed";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 6 }).format(Number(value) * multiplier);
}

function isFree(model: Model) {
  return Number(model.pricing.prompt) === 0 && Number(model.pricing.completion) === 0 && Number(model.pricing.request || 0) === 0;
}

export default function ModelSelector({ value, onChange, onLoad, disabled }: { value: string; onChange: (model: string) => void; onLoad: (model: string) => void; disabled: boolean }) {
  const [models, setModels] = useState<Model[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [query, setQuery] = useState("");
  const [freeOnly, setFreeOnly] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void (async () => {
      try {
        const response = await fetch("/api/settings/model", { cache: "no-store", signal: controller.signal });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || data.detail || "Unable to load models");
        if (!controller.signal.aborted) {
          setModels(data.models);
          onLoad(data.model);
        }
      } catch (error) {
        if (!controller.signal.aborted) setError(error instanceof Error ? error.message : "Unable to load models");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [attempt, onLoad]);

  const selected = models.find((model) => model.id === value);
  const filtered = useMemo(() => models.filter((model) => (!freeOnly || isFree(model)) && `${model.name} ${model.id}`.toLowerCase().includes(query.toLowerCase())), [models, query, freeOnly]);

  return (
    <section
      className="space-y-3 rounded-xl border border-gray-200 p-4"
      aria-labelledby="model-heading"
    >
      <div>
        <h3
          id="model-heading"
          className="text-sm font-semibold text-gray-900"
        >
          AI model · OpenRouter
        </h3>
        <p className="mt-1 text-xs text-gray-500">Used for chat, tasks, email search, summaries, and email importance. Saved for this app, including background automations.</p>
      </div>
      {loading ? (
        <LoadingIndicator label="Loading models and pricing…" />
      ) : error ? (
        <div
          role="alert"
          className="text-sm text-red-600"
        >
          {error}{" "}
          <button
            type="button"
            className="underline"
            onClick={() => setAttempt((value) => value + 1)}
          >
            Retry
          </button>
        </div>
      ) : (
        <>
          <label
            className="block text-xs font-medium text-gray-700"
            htmlFor="model-search"
          >
            Search models
          </label>
          <input
            id="model-search"
            type="search"
            className="input"
            placeholder="Search by name or provider"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            disabled={disabled}
          />
          <label className="flex items-center gap-2 text-xs text-gray-600">
            <input
              type="checkbox"
              checked={freeOnly}
              onChange={(event) => setFreeOnly(event.target.checked)}
              disabled={disabled}
            />
            Show free models only
          </label>
          <label
            className="block text-xs font-medium text-gray-700"
            htmlFor="ai-model"
          >
            Model · input / output per 1M tokens
          </label>
          <select
            id="ai-model"
            className="w-full rounded-md border border-gray-300 bg-white p-2 text-sm"
            value={value}
            onChange={(event) => onChange(event.target.value)}
            disabled={disabled}
          >
            {!filtered.some((model) => model.id === value) && <option value={value}>{selected ? `${selected.name} (current selection)` : `${value} (unavailable)`}</option>}
            {filtered.map((model) => (
              <option
                key={model.id}
                value={model.id}
              >
                {model.name} — {isFree(model) ? "Free" : `${price(model.pricing.prompt, 1e6)} / ${price(model.pricing.completion, 1e6)}`}
              </option>
            ))}
          </select>
          {filtered.length === 0 && <p className="text-xs text-gray-500">No models match these filters.</p>}
          {selected ? (
            <div className="rounded-lg bg-gray-50 p-3 text-xs text-gray-600">
              <div className="break-all font-medium text-gray-900">{selected.id}</div>
              <dl className="mt-2 grid grid-cols-2 gap-2">
                <div>
                  <dt>Input / 1M tokens</dt>
                  <dd className="font-semibold">{price(selected.pricing.prompt, 1e6)}</dd>
                </div>
                <div>
                  <dt>Output / 1M tokens</dt>
                  <dd className="font-semibold">{price(selected.pricing.completion, 1e6)}</dd>
                </div>
                {Number(selected.pricing.request) > 0 && (
                  <div>
                    <dt>Per request</dt>
                    <dd>{price(selected.pricing.request)}</dd>
                  </div>
                )}
                {selected.pricing.input_cache_read !== undefined && (
                  <div>
                    <dt>Cached input / 1M tokens</dt>
                    <dd>{price(selected.pricing.input_cache_read, 1e6)}</dd>
                  </div>
                )}
                {selected.pricing.input_cache_write !== undefined && (
                  <div>
                    <dt>Cache write / 1M tokens</dt>
                    <dd>{price(selected.pricing.input_cache_write, 1e6)}</dd>
                  </div>
                )}
                {selected.context_length != null && (
                  <div>
                    <dt>Context window</dt>
                    <dd>{selected.context_length.toLocaleString()} tokens</dd>
                  </div>
                )}
              </dl>
            </div>
          ) : (
            <p className="text-xs text-amber-700">This model is no longer listed. Choose an available model to change it.</p>
          )}
          <p className="text-xs text-gray-500">USD list prices from OpenRouter, refreshed every 5 minutes. Actual charges depend on provider, usage, caching, and additional fees. One task may make multiple AI calls. Free models can have rate limits. Only models supporting text and tool calling are shown.</p>
          <a
            className="text-xs text-blue-600 underline"
            href="https://openrouter.ai/models"
            target="_blank"
            rel="noreferrer"
          >
            Compare models on OpenRouter
          </a>
        </>
      )}
    </section>
  );
}
