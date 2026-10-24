"use client";

import Image from "next/image";
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";

import styles from "./ModelSelector.module.css";

export type Model = {
  id: string;
  name: string;
  context_length: number | null;
  pricing: Record<string, string>;
};

export function price(value: string | undefined, multiplier = 1) {
  if (value === undefined || !Number.isFinite(Number(value)) || Number(value) < 0) return "Not listed";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 6 }).format(Number(value) * multiplier);
}

export function isFree(model: Model) {
  return Number(model.pricing.prompt) === 0 && Number(model.pricing.completion) === 0 && Number(model.pricing.request || 0) === 0;
}

const providerIcons: Record<string, string> = {
  anthropic: "anthropic",
  openai: "openai",
  google: "google",
  deepseek: "deepseek",
  "meta-llama": "meta",
  meta: "meta",
  "bytedance-seed": "bytedance",
  "aion-labs": "aionlabs",
  "dots-studio": "dotsstudio",
  fireworks: "fireworks",
  perceptron: "perceptron",
  poolside: "poolside",
  rekaai: "reka",
  relace: "relace",
  sakana: "sakana",
  tencent: "tencent",
  upstage: "upstage",
  mistralai: "mistral",
  "x-ai": "xai",
  qwen: "alibaba",
  cohere: "cohere",
  moonshotai: "moonshotai",
  minimax: "minimax",
  nvidia: "nvidia",
  perplexity: "perplexity",
  openrouter: "openrouter",
  ai21: "ai21",
  "arcee-ai": "arcee",
  inception: "inception",
  liquid: "liquid",
  stepfun: "stepfun",
  "z-ai": "zai",
  xiaomi: "xiaomi",
  amazon: "amazon-bedrock",
  "ibm-granite": "watsonx",
};

export function ProviderIcon({ modelId }: { modelId: string }) {
  const provider = modelId.split("/")[0].replace(/^~+/, "");
  const icon = providerIcons[provider];
  const [failedIcon, setFailedIcon] = useState("");

  return (
    <span
      className={styles.providerIcon}
      aria-hidden="true"
    >
      {icon && failedIcon !== icon ? (
        <Image
          src={`/model-providers/${icon}.svg`}
          alt=""
          width={20}
          height={20}
          onError={() => setFailedIcon(icon)}
        />
      ) : (
        <span>{provider.slice(0, 2).toUpperCase() || "AI"}</span>
      )}
    </span>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      className={styles.chevron}
      data-open={open}
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
    >
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

/** Searchable picker over the OpenRouter catalog. */
export default function ModelSelector({ models, value, onChange, disabled }: { models: Model[]; value: string; onChange: (modelId: string) => void; disabled: boolean }) {
  const [query, setQuery] = useState("");
  const [freeOnly, setFreeOnly] = useState(false);
  const [open, setOpen] = useState(false);
  const [present, setPresent] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [dropdownHeight, setDropdownHeight] = useState(300);
  const pickerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const id = useId();

  const selected = models.find((model) => model.id === value);
  const filtered = useMemo(() => models.filter((model) => (!freeOnly || isFree(model)) && `${model.name} ${model.id}`.toLowerCase().includes(query.trim().toLowerCase())), [models, query, freeOnly]);

  useLayoutEffect(() => {
    if (!open) return;
    const trigger = triggerRef.current;
    const panel = trigger?.closest('[role="tabpanel"], [data-dropdown-boundary]');
    if (!trigger || !panel) return;
    const resize = () => setDropdownHeight(Math.max(0, Math.min(320, panel.getBoundingClientRect().bottom - trigger.getBoundingClientRect().bottom - 12)));
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(panel);
    panel.addEventListener("scroll", resize);
    searchRef.current?.focus({ preventScroll: true });
    const list = listRef.current;
    const option = list?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (list && option) list.scrollTop = option.offsetTop;
    return () => {
      observer.disconnect();
      panel.removeEventListener("scroll", resize);
    };
  }, [open]);

  useEffect(() => {
    const list = listRef.current;
    const option = list?.children[activeIndex] as HTMLElement | undefined;
    if (!open || !list || !option) return;
    const top = option.offsetTop;
    if (top < list.scrollTop) list.scrollTop = top;
    else if (top + option.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = top + option.offsetHeight - list.clientHeight;
  }, [activeIndex, open, dropdownHeight, filtered]);

  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  useEffect(() => {
    if (open || !present) return;
    const timeout = window.setTimeout(() => setPresent(false), window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 200);
    return () => window.clearTimeout(timeout);
  }, [open, present]);

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !pickerRef.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);

  function closePicker() {
    setOpen(false);
    triggerRef.current?.focus();
  }

  function selectModel(model: Model) {
    onChange(model.id);
    closePicker();
  }

  function openPicker() {
    setQuery("");
    setFreeOnly(false);
    setActiveIndex(
      Math.max(
        0,
        models.findIndex((model) => model.id === value),
      ),
    );
    setPresent(true);
    setOpen(true);
  }

  return (
    <>
      <div
        ref={pickerRef}
        className={styles.picker}
        data-open={open}
        onBlur={(event) => {
          if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) setOpen(false);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape" && open) {
            event.preventDefault();
            event.stopPropagation();
            closePicker();
          }
        }}
      >
        <button
          ref={triggerRef}
          type="button"
          className={styles.trigger}
          aria-label={`Choose AI model: ${selected?.name || value}`}
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-controls={`${id}-list`}
          disabled={disabled}
          onClick={() => (open ? setOpen(false) : openPicker())}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              openPicker();
            }
          }}
        >
          <ProviderIcon modelId={value} />
          <span className={styles.modelIdentity}>
            <span className={styles.eyebrow}>Selected model</span>
            <span className={styles.modelName}>{selected?.name || value || "Choose a model"}</span>
          </span>
          {selected && isFree(selected) && <span className={styles.freeBadge}>Free</span>}
          <Chevron open={open} />
        </button>
        {present && (
          <div
            className={`${styles.dropdown} menu-popover`}
            data-state={open ? "open" : "closed"}
            aria-hidden={!open}
            onAnimationEnd={(event) => {
              if (event.target === event.currentTarget && !open) setPresent(false);
            }}
            style={{ maxHeight: dropdownHeight }}
          >
            <div className={styles.searchRow}>
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                aria-hidden="true"
              >
                <circle
                  cx="10.5"
                  cy="10.5"
                  r="6.5"
                />
                <path d="m16 16 4 4" />
              </svg>
              <input
                ref={searchRef}
                disabled={!open}
                role="combobox"
                aria-label="Search models by name or provider"
                aria-expanded={open}
                aria-controls={`${id}-list`}
                aria-autocomplete="list"
                aria-activedescendant={filtered[activeIndex] ? `${id}-option-${activeIndex}` : undefined}
                placeholder="Search models or providers…"
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setActiveIndex(0);
                }}
                onKeyDown={(event) => {
                  if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) && (event.key.startsWith("Arrow") || event.ctrlKey)) {
                    event.preventDefault();
                    setActiveIndex((index) => (event.key === "Home" ? 0 : event.key === "End" ? Math.max(0, filtered.length - 1) : Math.max(0, Math.min(filtered.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))));
                  } else if (event.key === "Enter") {
                    event.preventDefault();
                    if (filtered[activeIndex]) selectModel(filtered[activeIndex]);
                  }
                }}
              />
            </div>
            <div className={styles.filterRow}>
              <label
                htmlFor={`${id}-free-only`}
                onMouseDown={(event) => event.preventDefault()}
              >
                <input
                  id={`${id}-free-only`}
                  type="checkbox"
                  disabled={!open}
                  checked={freeOnly}
                  onChange={(event) => {
                    setFreeOnly(event.target.checked);
                    setActiveIndex(0);
                  }}
                />
                Free only
              </label>
              <span>{filtered.length} models</span>
            </div>
            <div
              id={`${id}-list`}
              ref={listRef}
              role="listbox"
              tabIndex={-1}
              aria-label="AI models"
              className={styles.options}
            >
              {filtered.map((model, index) => (
                <div
                  key={model.id}
                  id={`${id}-option-${index}`}
                  role="option"
                  aria-selected={model.id === value}
                  className={styles.option}
                  data-active={activeIndex === index}
                  onMouseEnter={() => setActiveIndex(index)}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => selectModel(model)}
                >
                  <ProviderIcon modelId={model.id} />
                  <span className={styles.modelIdentity}>
                    <span
                      className={styles.optionName}
                      title={model.name}
                    >
                      {model.name}
                    </span>
                    <span className={styles.modelId}>{model.id}</span>
                  </span>
                  {isFree(model) ? (
                    <span className={styles.freeBadge}>Free</span>
                  ) : (
                    <span className={styles.optionPrice}>
                      <span>
                        {price(model.pricing.prompt, 1e6)} / {price(model.pricing.completion, 1e6)}
                      </span>
                      <span>in / out · per 1M</span>
                    </span>
                  )}
                  <span
                    className={styles.check}
                    aria-hidden="true"
                  >
                    {model.id === value ? "✓" : ""}
                  </span>
                </div>
              ))}
            </div>
            {filtered.length === 0 && <p className={styles.empty}>No models found. Try another search or turn off “Free only”.</p>}
          </div>
        )}
      </div>
      {selected ? (
        <div className={styles.summary}>
          <div className={styles.summaryHeading}>
            <h4>At a glance</h4>
            <span>USD / 1M tokens</span>
          </div>
          <dl className={styles.stats}>
            <div>
              <dt>Input</dt>
              <dd>{price(selected.pricing.prompt, 1e6)}</dd>
            </div>
            <div>
              <dt>Output</dt>
              <dd>{price(selected.pricing.completion, 1e6)}</dd>
            </div>
            <div>
              <dt>Context window</dt>
              <dd>{selected.context_length != null ? new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(selected.context_length) : "Not listed"}</dd>
            </div>
          </dl>
        </div>
      ) : null}
      <details className={styles.details}>
        <summary>Pricing & details</summary>
        <div className={styles.detailsBody}>
          {selected && (
            <dl className={styles.extraPrices}>
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
                  <dt>Full context window</dt>
                  <dd>{selected.context_length.toLocaleString()} tokens</dd>
                </div>
              )}
            </dl>
          )}
          <p>OpenRouter list prices refresh every 5 minutes. Provider fees, caching, and usage can affect your bill. A task may use multiple AI calls. Free models may have rate limits.</p>
          <p>Only models with text and tool support are shown.</p>
          <a
            href="https://openrouter.ai/models"
            target="_blank"
            rel="noreferrer"
          >
            Compare models on OpenRouter ↗
          </a>
        </div>
      </details>
    </>
  );
}
