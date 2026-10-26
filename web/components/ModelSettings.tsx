"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";

import ModelSelector, { ProviderIcon, type Model } from "./ModelSelector";
import { AnimatedDialog } from "./ui/AnimatedDialog";
import { LoadingIndicator, Spinner } from "./ui/LoadingIndicator";
import styles from "./ModelSelector.module.css";

type Source = "openrouter" | "openai_compatible";

type SavedModel = {
  id: string;
  kind: Source;
  model: string;
  name: string;
  base_url?: string;
  has_api_key?: boolean;
  context_length?: number;
  discovered_context_length?: number | null;
  context_length_override?: number;
  discovery_metadata?: Record<string, unknown>;
};

type DiscoveredModel = Model & { metadata?: Record<string, unknown> };
type EndpointValues = { base_url: string; api_key?: string; model: string; name: string; context_length_override: number | null };

type Listing = { active: string | null; models: SavedModel[]; openrouter_key_configured: boolean };

const sources = [
  { id: "openrouter", label: "OpenRouter" },
  { id: "openai_compatible", label: "OpenAI-compatible" },
] as const;

async function request<T>(path = "", init?: RequestInit & { json?: unknown }): Promise<T> {
  const response = await fetch(`/api/settings/models${path}`, {
    cache: "no-store",
    ...init,
    headers: init?.json === undefined ? undefined : { "Content-Type": "application/json" },
    body: init?.json === undefined ? undefined : JSON.stringify(init.json),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.detail || "Something went wrong. Try again.");
  return data as T;
}

function host(baseUrl = "") {
  try {
    return new URL(baseUrl).host;
  } catch {
    return baseUrl;
  }
}

function EndpointFields({ endpoints, initial, disabled, onReady }: { endpoints: SavedModel[]; initial?: SavedModel; disabled: boolean; onReady: (fields: EndpointValues | null) => void }) {
  const [baseUrl, setBaseUrl] = useState(() => initial?.base_url ?? endpoints[endpoints.length - 1]?.base_url ?? "");
  const [apiKey, setApiKey] = useState("");
  const [name, setName] = useState(initial?.name ?? "");
  const [models, setModels] = useState<DiscoveredModel[]>(initial ? [{ id: initial.model, name: initial.model, context_length: initial.discovered_context_length ?? (initial.context_length_override ? null : initial.context_length) ?? null, pricing: {}, metadata: initial.discovery_metadata }] : []);
  const [overrides, setOverrides] = useState<Record<string, string>>(initial?.context_length_override ? { [initial.model]: String(initial.context_length_override) } : {});
  const [model, setModel] = useState(initial?.model ?? "");
  const [discovering, setDiscovering] = useState(false);
  const [error, setError] = useState("");
  const id = useId();
  const savedKey = endpoints.some((endpoint) => endpoint.has_api_key && [endpoint.base_url, endpoint.base_url?.replace(/\/v1$/, "")].includes(baseUrl.trim().replace(/\/$/, "")));
  const busy = disabled || discovering;
  const selected = models.find((option) => option.id === model);
  const contextWindow = overrides[model] ?? "";
  const contextOverride = contextWindow.trim() ? Number(contextWindow) : null;
  const validContext = contextOverride === null || (Number.isSafeInteger(contextOverride) && contextOverride >= 1024);

  useEffect(() => {
    onReady(model && baseUrl.trim() && validContext ? { base_url: baseUrl.trim(), ...(apiKey && { api_key: apiKey }), model, name, context_length_override: contextOverride } : null);
  }, [apiKey, baseUrl, model, name, contextOverride, validContext, onReady]);

  async function discover() {
    setDiscovering(true);
    setError("");
    try {
      const data = await request<{ base_url: string; models: DiscoveredModel[] }>("/discover", { method: "POST", json: { base_url: baseUrl.trim(), ...(apiKey && { api_key: apiKey }) } });
      const ids = data.models.map((option) => option.id);
      setBaseUrl(data.base_url);
      setModels(data.models);
      setModel((current) => (ids.includes(current) ? current : ids.length === 1 ? ids[0] : ""));
    } catch (error) {
      setModels([]);
      setModel("");
      setError(error instanceof Error ? error.message : "Unable to discover models");
    } finally {
      setDiscovering(false);
    }
  }

  return (
    <form
      className={styles.localForm}
      onSubmit={(event) => {
        event.preventDefault();
        if (baseUrl.trim()) void discover();
      }}
    >
      <div className={styles.field}>
        <label htmlFor={`${id}-url`}>Base URL</label>
        <input
          id={`${id}-url`}
          className="input"
          type="url"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          placeholder="https://api.example.com/v1"
          value={baseUrl}
          disabled={busy}
          onChange={(event) => {
            setBaseUrl(event.target.value);
            setModels([]);
            setModel("");
            setOverrides({});
          }}
        />
        <p>Any OpenAI-compatible API: a hosted provider, or a server you run like Ollama, LM Studio, or vLLM.</p>
      </div>
      <div className={styles.field}>
        <label htmlFor={`${id}-key`}>
          API key <span>Optional</span>
        </label>
        <input
          id={`${id}-key`}
          className="input"
          type="password"
          autoComplete="off"
          placeholder={savedKey ? "Leave blank to reuse saved key" : "Only if the server requires one"}
          value={apiKey}
          disabled={busy}
          onChange={(event) => {
            setApiKey(event.target.value);
            setModels([]);
            setModel("");
            setOverrides({});
          }}
        />
      </div>
      <button
        type="submit"
        className={styles.discoverButton}
        disabled={busy || !baseUrl.trim()}
      >
        {discovering ? "Discovering…" : models.length ? "Refresh models" : "Discover models"}
      </button>
      {error && (
        <p
          role="alert"
          className={styles.error}
        >
          {error}
        </p>
      )}
      {models.length > 0 && (
        <div className={styles.discovered}>
          <div className={styles.field}>
            <label htmlFor={`${id}-model`}>Model</label>
            <select
              id={`${id}-model`}
              className="input"
              value={model}
              disabled={busy}
              onChange={(event) => setModel(event.target.value)}
            >
              {!model && (
                <option
                  value=""
                  disabled
                >
                  Choose a model
                </option>
              )}
              {models.map((option) => (
                <option
                  key={option.id}
                  value={option.id}
                >
                  {option.name}
                </option>
              ))}
            </select>
            <p role="status">
              {models.length} {models.length === 1 ? "model" : "models"} found.
            </p>
          </div>
          {selected && (
            <>
              <div className={styles.field}>
                <label htmlFor={`${id}-context`}>
                  Context window (tokens) <span>Optional override</span>
                </label>
                <input
                  id={`${id}-context`}
                  className="input"
                  type="number"
                  min={1024}
                  step={1}
                  placeholder={selected.context_length ? String(selected.context_length) : "Use application default"}
                  value={contextWindow}
                  disabled={busy}
                  aria-invalid={!validContext}
                  aria-describedby={`${id}-context-help`}
                  onChange={(event) => setOverrides((current) => ({ ...current, [model]: event.target.value }))}
                />
                <p id={`${id}-context-help`}>
                  {selected.context_length ? `Discovered: ${selected.context_length.toLocaleString()} tokens.` : "The server did not report a recognized context-window size."} {contextOverride !== null ? "Your override will be used. Clear it to use automatic settings." : "Leave blank to use the discovered size, or the application default when unavailable."} Use the context size configured on your server.
                </p>
                {!validContext && (
                  <p
                    role="alert"
                    className={styles.error}
                  >
                    Enter a whole number of at least 1,024 tokens.
                  </p>
                )}
              </div>
              {selected.metadata ? (
                <details className={styles.details}>
                  <summary>Discovery data for {selected.id}</summary>
                  <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-md bg-gray-50 p-3 text-xs">{JSON.stringify(selected.metadata, null, 2)}</pre>
                </details>
              ) : (
                <p className="text-xs text-gray-500">Refresh models to see the server’s discovery data for this model.</p>
              )}
            </>
          )}
          <div className={styles.field}>
            <label htmlFor={`${id}-name`}>
              Display name <span>Optional</span>
            </label>
            <input
              id={`${id}-name`}
              className="input"
              type="text"
              autoComplete="off"
              maxLength={120}
              placeholder={model || "e.g. Home server"}
              value={name}
              disabled={busy}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
        </div>
      )}
    </form>
  );
}

function ModelForm({ initial, catalog, catalogError, onRetryCatalog, endpoints, titleId, saving, setSaving, onSaved, onClose }: { initial?: SavedModel; catalog: Model[] | null; catalogError: string; onRetryCatalog: () => void; endpoints: SavedModel[]; titleId: string; saving: boolean; setSaving: (saving: boolean) => void; onSaved: (listing: Listing) => void; onClose: () => void }) {
  const [source, setSource] = useState<Source>(initial?.kind ?? "openrouter");
  const [openRouterModel, setOpenRouterModel] = useState(initial?.model ?? "");
  const [name, setName] = useState(initial?.name ?? "");
  const [endpoint, setEndpoint] = useState<EndpointValues | null>(null);
  const [activate, setActivate] = useState(true);
  const [error, setError] = useState("");
  const ready = source === "openrouter" ? Boolean(openRouterModel) : Boolean(endpoint);

  async function save() {
    setSaving(true);
    setError("");
    try {
      const body = source === "openrouter" ? { kind: source, model: openRouterModel, name } : { kind: source, ...endpoint };
      onSaved(await request<Listing>(initial ? `/${encodeURIComponent(initial.id)}` : "", { method: initial ? "PUT" : "POST", json: { ...body, activate } }));
    } catch (error) {
      setError(error instanceof Error ? error.message : initial ? "Unable to save model" : "Unable to add model");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <div className="mb-1 flex shrink-0 items-center justify-between">
        <h2
          id={titleId}
          className="text-lg font-semibold"
        >
          {initial ? "Edit model" : "Add a model"}
        </h2>
        <button
          onClick={onClose}
          disabled={saving}
          className="rounded-md p-2 hover:bg-gray-100"
          aria-label={initial ? "Close edit model" : "Close add model"}
        >
          ✕
        </button>
      </div>
      <p className="mb-4 shrink-0 text-sm text-gray-600">Saved models can be switched to at any time from the Model tab.</p>
      <div
        role="radiogroup"
        aria-label="Model source"
        className={styles.providerSwitch}
      >
        {sources.map((option) => (
          <button
            key={option.id}
            type="button"
            role="radio"
            aria-checked={source === option.id}
            disabled={saving}
            onClick={() => {
              setSource(option.id);
              setError("");
            }}
          >
            {option.label}
          </button>
        ))}
      </div>
      <div
        key={source}
        className={styles.addBody}
        data-dropdown-boundary
      >
        {source === "openrouter" ? (
          catalog ? (
            <>
              <ModelSelector
                models={catalog}
                value={openRouterModel}
                onChange={setOpenRouterModel}
                disabled={saving}
              />
              <div className={styles.field}>
                <label htmlFor={`${titleId}-name`}>
                  Display name <span>Optional</span>
                </label>
                <input
                  id={`${titleId}-name`}
                  className="input"
                  value={name}
                  maxLength={120}
                  disabled={saving}
                  onChange={(event) => setName(event.target.value)}
                />
              </div>
            </>
          ) : catalogError ? (
            <div
              role="alert"
              className={styles.error}
            >
              {catalogError}{" "}
              <button
                type="button"
                onClick={onRetryCatalog}
              >
                Try again
              </button>
            </div>
          ) : (
            <LoadingIndicator label="Loading OpenRouter models…" />
          )
        ) : (
          <EndpointFields
            endpoints={endpoints}
            initial={initial?.kind === source ? initial : undefined}
            disabled={saving}
            onReady={setEndpoint}
          />
        )}
      </div>
      {error && (
        <p
          role="alert"
          className={`${styles.error} shrink-0`}
        >
          {error}
        </p>
      )}
      <div className={styles.addActions}>
        {!initial && (
          <label>
            <input
              type="checkbox"
              checked={activate}
              disabled={saving}
              onChange={(event) => setActivate(event.target.checked)}
            />
            Use this model now
          </label>
        )}
        <button
          type="button"
          className="rounded-md px-4 py-2 text-sm text-gray-700 hover:bg-gray-100"
          disabled={saving}
          onClick={onClose}
        >
          Cancel
        </button>
        <button
          type="button"
          className="btn gap-2"
          disabled={saving || !ready}
          aria-busy={saving}
          onClick={() => void save()}
        >
          {saving && <Spinner className="rounded-full bg-white p-0.5" />}
          {saving ? (initial ? "Saving…" : "Adding…") : initial ? "Save changes" : "Add model"}
        </button>
      </div>
    </>
  );
}

function ModelDialog({ open, onClose, ...form }: { open: boolean; onClose: () => void; initial?: SavedModel; catalog: Model[] | null; catalogError: string; onRetryCatalog: () => void; endpoints: SavedModel[]; onSaved: (listing: Listing) => void }) {
  const [saving, setSaving] = useState(false);
  const titleId = useId();

  return (
    <AnimatedDialog
      open={open}
      onClose={onClose}
      dismissible={!saving}
      labelledBy={titleId}
      panelClassName={`${styles.addDialog} !max-w-xl !overflow-hidden`}
    >
      {/* The dialog unmounts its contents once closed, so each open starts with a fresh form. */}
      <ModelForm
        {...form}
        titleId={titleId}
        saving={saving}
        setSaving={setSaving}
        onClose={onClose}
      />
    </AnimatedDialog>
  );
}

const exitDuration = 180;

function wait(duration: number) {
  return new Promise((resolve) => window.setTimeout(resolve, window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : duration));
}

function RowMenu({ label, active, disabled, onUse, onEdit, onRemove }: { label: string; active: boolean; disabled: boolean; onUse: () => void; onEdit: () => void; onRemove: () => void }) {
  const [open, setOpen] = useState(false);
  const [present, setPresent] = useState(false);
  const [placement, setPlacement] = useState<"bottom" | "top">("bottom");
  const menuId = useId();
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const initialFocus = useRef(0);

  useEffect(() => {
    if (!open) return;
    menuRef.current?.querySelectorAll<HTMLButtonElement>("button")[initialFocus.current]?.focus();
    const dismiss = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);

  useEffect(() => {
    if (open) {
      setPresent(true);
      return;
    }
    if (!present) return;
    const timeout = window.setTimeout(() => setPresent(false), window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 200);
    return () => window.clearTimeout(timeout);
  }, [open, present]);

  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  function show(focusIndex: number) {
    // Open upward when the scrolling panel has no room for the menu below the row.
    const trigger = triggerRef.current;
    const boundary = trigger?.closest('[role="tabpanel"]');
    if (trigger && boundary) setPlacement(boundary.getBoundingClientRect().bottom - trigger.getBoundingClientRect().bottom < (active ? 52 : 132) ? "top" : "bottom");
    initialFocus.current = focusIndex;
    setOpen(true);
  }

  function close() {
    setOpen(false);
    triggerRef.current?.focus();
  }

  function navigate(event: KeyboardEvent<HTMLDivElement>) {
    const buttons = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>("button") || []);
    const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const index = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : event.key === "ArrowDown" ? (current + 1) % buttons.length : event.key === "ArrowUp" ? (current - 1 + buttons.length) % buttons.length : null;
    if (index !== null) {
      event.preventDefault();
      buttons[index]?.focus();
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
  }

  return (
    <div
      ref={containerRef}
      className={styles.menuContainer}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        className={styles.menuTrigger}
        aria-label={`Actions for ${label}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : show(0))}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            show(event.key === "ArrowUp" ? (active ? 0 : 2) : 0);
          }
        }}
      >
        <svg
          viewBox="0 0 24 24"
          width="20"
          height="20"
          fill="currentColor"
          aria-hidden="true"
        >
          <circle
            cx="5"
            cy="12"
            r="1.75"
          />
          <circle
            cx="12"
            cy="12"
            r="1.75"
          />
          <circle
            cx="19"
            cy="12"
            r="1.75"
          />
        </svg>
      </button>
      {(open || present) && (
        <div
          ref={menuRef}
          id={menuId}
          className={styles.menu}
          data-state={open ? "open" : "closed"}
          data-placement={placement}
          aria-hidden={!open}
          onAnimationEnd={(event) => {
            if (event.target === event.currentTarget && !open) setPresent(false);
          }}
          role="menu"
          aria-label={`Actions for ${label}`}
          onKeyDown={navigate}
        >
          <button
            type="button"
            role="menuitem"
            disabled={!open}
            tabIndex={-1}
            onClick={() => {
              close();
              onEdit();
            }}
          >
            Edit
          </button>
          {!active && (
            <button
              type="button"
              role="menuitem"
              disabled={!open}
              tabIndex={-1}
              onClick={() => {
                close();
                onUse();
              }}
            >
              Use this model
            </button>
          )}
          {!active && (
            <button
              type="button"
              role="menuitem"
              disabled={!open}
              tabIndex={-1}
              className={styles.menuDanger}
              onClick={() => {
                close();
                onRemove();
              }}
            >
              Remove
            </button>
          )}
        </div>
      )}
    </div>
  );
}

type Removal = { id: string; name: string; hasApiKey: boolean };

function RemoveModelConfirm({ removal, titleId, removing, setRemoving, onRemoved, onCancel }: { removal: Removal; titleId: string; removing: boolean; setRemoving: (removing: boolean) => void; onRemoved: (listing: Listing) => void; onCancel: () => void }) {
  const [error, setError] = useState("");

  async function remove() {
    setRemoving(true);
    setError("");
    try {
      onRemoved(await request<Listing>(`/${encodeURIComponent(removal.id)}`, { method: "DELETE" }));
    } catch (error) {
      setError(error instanceof Error ? error.message : "Unable to remove model");
    } finally {
      setRemoving(false);
    }
  }

  return (
    <>
      <div
        className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-red-50 text-red-600"
        aria-hidden="true"
      >
        <svg
          width="24"
          height="24"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.75"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M3 6h18M9 6V4h6v2M5 6l1 14h12l1-14M10 10v6M14 10v6" />
        </svg>
      </div>
      <h2
        id={titleId}
        className="text-lg font-semibold text-gray-900 [overflow-wrap:anywhere]"
      >
        Remove {removal.name}?
      </h2>
      <p className="mt-2 text-sm leading-6 text-gray-600">It will no longer appear in your saved models. You can add it again at any time.</p>
      {removal.hasApiKey && <p className="mt-2 text-sm text-gray-600">The API key saved with this model will be deleted.</p>}
      {error && (
        <p
          role="alert"
          className="mt-4 text-sm text-red-700"
        >
          {error}
        </p>
      )}
      <div className="mt-6 flex flex-wrap justify-end gap-2 border-t border-gray-200 pt-4">
        <button
          type="button"
          autoFocus
          onClick={onCancel}
          disabled={removing}
          className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => void remove()}
          disabled={removing}
          aria-busy={removing}
          className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {removing && <Spinner />}
          {removing ? "Removing…" : "Remove"}
        </button>
      </div>
    </>
  );
}

function RemoveModelDialog({ open, removal, onCancel, onRemoved }: { open: boolean; removal: Removal | null; onCancel: () => void; onRemoved: (listing: Listing) => void }) {
  const [removing, setRemoving] = useState(false);
  const titleId = useId();

  return (
    <AnimatedDialog
      open={open}
      onClose={onCancel}
      dismissible={!removing}
      labelledBy={titleId}
      panelClassName="!max-w-md"
    >
      {removal && (
        <RemoveModelConfirm
          removal={removal}
          titleId={titleId}
          removing={removing}
          setRemoving={setRemoving}
          onRemoved={onRemoved}
          onCancel={onCancel}
        />
      )}
    </AnimatedDialog>
  );
}

/** Saved models and which one is active. Changes apply immediately, independent of the settings Save button. */
export default function ModelSettings({ disabled }: { disabled: boolean }) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [loadError, setLoadError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [catalog, setCatalog] = useState<Model[] | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [catalogAttempt, setCatalogAttempt] = useState(0);
  const [pending, setPending] = useState("");
  const [leaving, setLeaving] = useState("");
  const [entering, setEntering] = useState("");
  const [actionError, setActionError] = useState("");
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<SavedModel | undefined>();
  const [editingOpen, setEditingOpen] = useState(false);
  const [removal, setRemoval] = useState<Removal | null>(null);
  const [confirmingRemoval, setConfirmingRemoval] = useState(false);
  const rows = useRef(new Map<string, HTMLLIElement>());
  const rowPositions = useRef(new Map<string, number>());
  const id = useId();

  useEffect(() => {
    const controller = new AbortController();
    setLoadError("");
    request<Listing>("", { signal: controller.signal })
      .then(setListing)
      .catch((error) => {
        if (!controller.signal.aborted) setLoadError(error instanceof Error ? error.message : "Unable to load models");
      });
    return () => controller.abort();
  }, [attempt]);

  useEffect(() => {
    const controller = new AbortController();
    setCatalogError("");
    request<{ models: Model[] }>("/openrouter", { signal: controller.signal })
      .then((data) => setCatalog(data.models))
      .catch((error) => {
        if (!controller.signal.aborted) setCatalogError(error instanceof Error ? error.message : "Unable to load OpenRouter models");
      });
    return () => controller.abort();
  }, [catalogAttempt]);

  // Slide rows from where they were to where they are now, so reordering and removals read as motion.
  useLayoutEffect(() => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    rows.current.forEach((row, modelId) => {
      if (!row.offsetParent) return;
      const top = row.offsetTop;
      const previous = rowPositions.current.get(modelId);
      if (!reduceMotion && previous !== undefined && previous !== top) {
        row.animate([{ transform: `translateY(${previous - top}px)` }, { transform: "none" }], { duration: 320, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" });
      }
      rowPositions.current.set(modelId, top);
    });
  });

  async function activate(modelId: string) {
    setPending(modelId);
    setActionError("");
    try {
      setListing(await request<Listing>("/active", { method: "PUT", json: { id: modelId } }));
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Unable to switch models");
    } finally {
      setPending("");
    }
  }

  async function finishRemoval(modelId: string, next: Listing) {
    setConfirmingRemoval(false);
    setActionError("");
    // Let the dialog close, fade the row out, then drop it so the rows below slide up.
    await wait(exitDuration);
    setLeaving(modelId);
    await wait(exitDuration);
    setLeaving("");
    setListing(next);
  }

  const busy = disabled || Boolean(pending) || Boolean(leaving);
  const active = listing?.models.find((model) => model.id === listing.active);
  const ordered = listing ? [...listing.models].sort((a, b) => Number(b.id === listing.active) - Number(a.id === listing.active)) : [];

  return (
    <section
      className={styles.section}
      aria-labelledby={`${id}-heading`}
    >
      <header className={styles.header}>
        <div className={styles.titleRow}>
          <h3 id={`${id}-heading`}>AI model</h3>
        </div>
        <p>The active model runs your chats and automations. Changes here apply right away.</p>
      </header>
      {loadError ? (
        <div
          role="alert"
          className={styles.error}
        >
          {loadError}{" "}
          <button
            type="button"
            onClick={() => setAttempt((value) => value + 1)}
          >
            Try again
          </button>
        </div>
      ) : !listing ? (
        <LoadingIndicator label="Loading models…" />
      ) : (
        <>
          <div className={styles.savedHeading}>
            <h4>Saved models</h4>
            <button
              type="button"
              className={styles.secondaryButton}
              disabled={busy}
              onClick={() => setAdding(true)}
            >
              Add model
            </button>
          </div>
          {ordered.length ? (
            <ul
              className={styles.savedList}
              aria-label="Saved models"
            >
              {ordered.map((model) => {
                const isActive = model.id === listing.active;
                const name = model.name || catalog?.find((option) => option.id === model.model)?.name || model.model;
                const source = model.kind === "openrouter" ? "OpenRouter" : host(model.base_url);
                return (
                  <li
                    key={model.id}
                    ref={(row) => {
                      if (row) rows.current.set(model.id, row);
                      else {
                        rows.current.delete(model.id);
                        rowPositions.current.delete(model.id);
                      }
                    }}
                    className={styles.savedModel}
                    data-active={isActive}
                    data-entering={model.id === entering}
                    data-leaving={model.id === leaving}
                  >
                    <ProviderIcon modelId={model.model} />
                    <span className={styles.modelIdentity}>
                      <span className={styles.savedName}>{name}</span>
                      <span className={styles.modelId}>{name === model.model ? source : `${source} · ${model.model}`}</span>
                    </span>
                    {isActive && <span className={styles.activeBadge}>Active</span>}
                    {pending === model.id ? (
                      <span
                        className={styles.menuTrigger}
                        role="status"
                        aria-label={`Switching to ${name}`}
                      >
                        <Spinner />
                      </span>
                    ) : (
                      <RowMenu
                        label={name}
                        active={isActive}
                        disabled={busy}
                        onEdit={() => {
                          setEditing(model);
                          setEditingOpen(true);
                        }}
                        onUse={() => void activate(model.id)}
                        onRemove={() => {
                          setRemoval({ id: model.id, name, hasApiKey: Boolean(model.has_api_key) });
                          setConfirmingRemoval(true);
                        }}
                      />
                    )}
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className={styles.empty}>No saved models yet. Add one to get started.</p>
          )}
          {active?.kind === "openrouter" && !listing.openrouter_key_configured && <p className={styles.warning}>OPENROUTER_API_KEY isn’t set, so the active OpenRouter model can’t run. Set the key, or add and use an OpenAI-compatible model.</p>}
          {actionError && (
            <p
              role="alert"
              className={styles.error}
            >
              {actionError}
            </p>
          )}
        </>
      )}
      <ModelDialog
        open={adding}
        onClose={() => setAdding(false)}
        catalog={catalog}
        catalogError={catalogError}
        onRetryCatalog={() => setCatalogAttempt((value) => value + 1)}
        endpoints={listing?.models.filter((model) => model.kind === "openai_compatible") ?? []}
        onSaved={(next) => {
          setEntering(next.models.find((model) => !listing?.models.some((previous) => previous.id === model.id))?.id ?? "");
          setListing(next);
          setAdding(false);
        }}
      />
      <ModelDialog
        open={editingOpen}
        onClose={() => setEditingOpen(false)}
        initial={editing}
        catalog={catalog}
        catalogError={catalogError}
        onRetryCatalog={() => setCatalogAttempt((value) => value + 1)}
        endpoints={listing?.models.filter((model) => model.kind === "openai_compatible") ?? []}
        onSaved={(next) => {
          setListing(next);
          setEditingOpen(false);
        }}
      />
      <RemoveModelDialog
        open={confirmingRemoval}
        removal={removal}
        onCancel={() => setConfirmingRemoval(false)}
        onRemoved={(next) => {
          if (removal) void finishRemoval(removal.id, next);
        }}
      />
    </section>
  );
}
