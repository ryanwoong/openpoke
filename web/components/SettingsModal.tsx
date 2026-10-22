"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Spinner } from "./ui/LoadingIndicator";
import ModelSelector from "./ModelSelector";
import { AnimatedDialog } from "./ui/AnimatedDialog";
import styles from "./SettingsModal.module.css";

export type GmailConnection = { connected: boolean; email: string; checking: boolean };

const settingsTabs = ["Main", "Model", "Integrations"] as const;

export type Settings = {
  timezone: string;
  chime: boolean;
  titleAlert: boolean;
};

export function useSettings() {
  const [settings, setSettings] = useState<Settings>({ timezone: "", chime: true, titleAlert: true });

  useEffect(() => {
    try {
      const timezone = localStorage.getItem("user_timezone") || "";
      const alerts = JSON.parse(localStorage.getItem("message_alerts") || "{}");
      setSettings({ timezone, chime: alerts?.chime !== false, titleAlert: alerts?.titleAlert !== false });
    } catch {}
  }, []);

  const persist = useCallback((s: Settings) => {
    setSettings(s);
    try {
      localStorage.setItem("user_timezone", s.timezone);
      localStorage.setItem("message_alerts", JSON.stringify({ chime: s.chime, titleAlert: s.titleAlert }));
    } catch {}
  }, []);

  return { settings, setSettings: persist } as const;
}

function coerceEmailFrom(value: unknown): string | null {
  if (typeof value === "string" && value.includes("@")) {
    return value;
  }
  if (value && typeof value === "object") {
    const candidate = (value as any).emailAddress ?? (value as any).email ?? (value as any).value ?? (value as any).address;
    if (typeof candidate === "string" && candidate.includes("@")) {
      return candidate;
    }
  }
  return null;
}

function deriveEmailFromPayload(payload: any): string {
  if (!payload) return "";
  const profileSlice = payload?.profile;
  const candidateObjects: any[] = [];

  if (profileSlice && typeof profileSlice === "object") {
    candidateObjects.push(profileSlice);
    if ((profileSlice as any).response_data && typeof (profileSlice as any).response_data === "object") {
      candidateObjects.push((profileSlice as any).response_data);
    }
    if (Array.isArray((profileSlice as any).items)) {
      for (const entry of (profileSlice as any).items as any[]) {
        if (entry && typeof entry === "object") {
          if (typeof entry.data === "object") candidateObjects.push(entry.data);
          if (typeof entry.response_data === "object") candidateObjects.push(entry.response_data);
          if (typeof entry.profile === "object") candidateObjects.push(entry.profile);
        }
      }
    }
  }

  const directCandidates = [payload?.email];

  for (const obj of candidateObjects) {
    if (!obj || typeof obj !== "object") continue;
    directCandidates.push(obj?.email, obj?.email_address, obj?.emailAddress, obj?.profile?.email, obj?.profile?.emailAddress, obj?.profile?.email_address, obj?.user?.email, obj?.user?.emailAddress, obj?.user?.email_address, obj?.data?.email, obj?.data?.emailAddress, obj?.data?.email_address);
    const emailAddresses = (obj as any).emailAddresses;
    if (Array.isArray(emailAddresses)) {
      for (const entry of emailAddresses) {
        const email = coerceEmailFrom(entry) ?? coerceEmailFrom((entry as any)?.value);
        if (email) return email;
      }
    }
  }

  for (const candidate of directCandidates) {
    const email = coerceEmailFrom(candidate);
    if (email) return email;
  }

  return "";
}

export default function SettingsModal({ open, onClose, settings, onSave, initialTab = "Main", onGmailStatusChange, onDataCleared, isChatBusy = false }: { open: boolean; onClose: () => void; settings: Settings; onSave: (s: Settings) => void; initialTab?: (typeof settingsTabs)[number]; onGmailStatusChange?: (status: GmailConnection) => void; onDataCleared?: () => void; isChatBusy?: boolean }) {
  const [activeTab, setActiveTab] = useState<(typeof settingsTabs)[number]>("Main");
  const [timezone, setTimezone] = useState(settings.timezone);
  const [alerts, setAlerts] = useState({ chime: settings.chime, titleAlert: settings.titleAlert });
  const [model, setModel] = useState("");
  const [savedModel, setSavedModel] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [isClearingData, setIsClearingData] = useState(false);
  const [clearDataError, setClearDataError] = useState("");
  const [dataCleared, setDataCleared] = useState(false);
  const loadModel = useCallback((value: string) => {
    setModel(value);
    setSavedModel(value);
  }, []);

  useEffect(() => {
    if (open) {
      setActiveTab(initialTab);
      setModel("");
      setSavedModel("");
      setSaveError("");
      setClearDataError("");
      setDataCleared(false);
    }
  }, [open, initialTab]);

  const handleClearData = async () => {
    if (isClearingData || isChatBusy) return;
    if (!window.confirm("Clear all conversation data? This permanently deletes your messages, agent history, reminders, and scheduled automations. This cannot be undone.")) return;
    setIsClearingData(true);
    setClearDataError("");
    setDataCleared(false);
    try {
      const response = await fetch("/api/chat/history", { method: "DELETE" });
      if (!response.ok) throw new Error("Unable to clear data. Please try again.");
      onDataCleared?.();
      setDataCleared(true);
    } catch (error) {
      setClearDataError(error instanceof Error ? error.message : "Unable to clear data. Please try again.");
    } finally {
      setIsClearingData(false);
    }
  };

  const saveSettings = async () => {
    setSaving(true);
    setSaveError("");
    try {
      if (model && model !== savedModel) {
        const response = await fetch("/api/settings/model", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ model }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || data.detail || "Unable to save model");
      }
      onSave({ timezone, ...alerts });
      onClose();
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "Unable to save settings");
    } finally {
      setSaving(false);
    }
  };
  const [connectingGmail, setConnectingGmail] = useState(false);
  const [isRefreshingGmail, setIsRefreshingGmail] = useState(true);
  const [isDisconnecting, setIsDisconnecting] = useState(false);
  const [gmailStatusMessage, setGmailStatusMessage] = useState("");
  const [gmailConnected, setGmailConnected] = useState(false);
  const [gmailEmail, setGmailEmail] = useState("");
  const [gmailConnId, setGmailConnId] = useState("");
  const [gmailProfile, setGmailProfile] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    onGmailStatusChange?.({ connected: gmailConnected, email: gmailEmail, checking: isRefreshingGmail });
  }, [gmailConnected, gmailEmail, isRefreshingGmail, onGmailStatusChange]);

  const readStoredUserId = useCallback(() => {
    if (typeof window === "undefined") return "";
    try {
      return localStorage.getItem("openpoke_user_id") || "";
    } catch {
      return "";
    }
  }, []);

  const ensureUserId = useCallback(() => {
    if (typeof window === "undefined") {
      return `web-${Math.random().toString(36).slice(2)}`;
    }
    try {
      const existing = localStorage.getItem("openpoke_user_id");
      if (existing) return existing;
      const cryptoObj = (globalThis as { crypto?: Crypto }).crypto;
      const randomPart = cryptoObj && typeof cryptoObj.randomUUID === "function" ? cryptoObj.randomUUID().replace(/-/g, "") : Math.random().toString(36).slice(2);
      const generated = `web-${randomPart}`;
      localStorage.setItem("openpoke_user_id", generated);
      return generated;
    } catch {
      return `web-${Math.random().toString(36).slice(2)}`;
    }
  }, []);

  const readStoredConnectionRequestId = useCallback(() => {
    if (gmailConnId) return gmailConnId;
    if (typeof window === "undefined") return "";
    try {
      return localStorage.getItem("gmail_connection_request_id") || "";
    } catch {
      return "";
    }
  }, [gmailConnId]);

  useEffect(() => {
    try {
      const savedConnected = localStorage.getItem("gmail_connected") === "true";
      const savedConnId = localStorage.getItem("gmail_connection_request_id") || "";
      const savedEmail = localStorage.getItem("gmail_email") || "";
      setGmailConnected(savedConnected);
      setGmailConnId(savedConnId);
      setGmailEmail(savedEmail);
      if (savedConnected && savedEmail) {
        setGmailStatusMessage(`Connected as ${savedEmail}`);
      }
    } catch {}
  }, []);

  const gmailProfileDetails = useMemo(() => {
    if (!gmailProfile) return [] as { label: string; value: string }[];
    const details: { label: string; value: string }[] = [];
    const messagesTotal = (gmailProfile as any)?.messagesTotal;
    if (typeof messagesTotal === "number") {
      details.push({ label: "Messages", value: messagesTotal.toLocaleString() });
    }
    const threadsTotal = (gmailProfile as any)?.threadsTotal;
    if (typeof threadsTotal === "number") {
      details.push({ label: "Threads", value: threadsTotal.toLocaleString() });
    }
    const historyId = (gmailProfile as any)?.historyId ?? (gmailProfile as any)?.historyID;
    if (historyId !== undefined && historyId !== null && historyId !== "") {
      details.push({ label: "History ID", value: String(historyId) });
    }
    return details;
  }, [gmailProfile]);

  const handleConnectGmail = useCallback(async () => {
    try {
      setConnectingGmail(true);
      setGmailStatusMessage("");
      const userId = ensureUserId();
      const resp = await fetch("/api/gmail/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ userId }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data?.ok) {
        const msg = data?.error || `Failed (${resp.status})`;
        setGmailStatusMessage(msg);
        return;
      }
      const url = data?.redirect_url;
      const connId = data?.connection_request_id || "";
      if (connId) {
        setGmailConnId(connId);
        try {
          localStorage.setItem("gmail_connection_request_id", connId);
        } catch {}
      }
      setGmailConnected(false);
      setGmailEmail("");
      setGmailProfile(null);
      if (url) {
        window.open(url, "_blank", "noopener");
        setGmailStatusMessage("Gmail authorization opened in a new tab. Complete it, then press “Refresh status”.");
      } else {
        setGmailStatusMessage("Connection initiated. Refresh status once authorization completes.");
      }
    } catch (e: any) {
      setGmailStatusMessage(e?.message || "Failed to connect Gmail");
    } finally {
      setConnectingGmail(false);
    }
  }, [ensureUserId]);

  const refreshGmailStatus = useCallback(async () => {
    const userId = readStoredUserId();
    const connectionRequestId = readStoredConnectionRequestId();
    if (!userId && !connectionRequestId) {
      setIsRefreshingGmail(false);
      setGmailConnected(false);
      setGmailProfile(null);
      setGmailEmail("");
      setGmailStatusMessage("Connect Gmail to get started.");
      return;
    }

    try {
      setIsRefreshingGmail(true);
      setGmailStatusMessage("Refreshing Gmail status…");
      const resp = await fetch("/api/gmail/status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ userId, connectionRequestId }),
      });
      const data = await resp.json().catch(() => ({}));

      if (!resp.ok || !data?.ok) {
        const message = data?.error || `Failed (${resp.status})`;
        setGmailConnected(false);
        setGmailProfile(null);
        setGmailEmail("");
        setGmailStatusMessage(message);
        return;
      }

      if (!gmailConnId && connectionRequestId) {
        setGmailConnId(connectionRequestId);
      }

      const profileData = data?.profile && typeof data.profile === "object" ? (data.profile as Record<string, unknown>) : null;
      setGmailProfile(profileData);

      const derivedEmail = deriveEmailFromPayload({ email: data?.email, profile: profileData });
      const email = derivedEmail || (typeof data?.email === "string" ? data.email : "");
      const connected = Boolean(data?.connected);

      setGmailConnected(connected);
      setGmailEmail(email);

      if (connected) {
        const source = typeof data?.profile_source === "string" ? data.profile_source : "";
        const sourceNote = source === "fetched" ? "Verified moments ago." : source === "cache" ? "Loaded from cache." : "";
        const message = email ? `Connected as ${email}` : "Gmail connected.";
        setGmailStatusMessage(sourceNote ? `${message} ${sourceNote}` : message);
        try {
          localStorage.setItem("gmail_connected", "true");
          if (email) localStorage.setItem("gmail_email", email);
          if (typeof data?.user_id === "string" && data.user_id) {
            localStorage.setItem("openpoke_user_id", data.user_id);
          }
        } catch {}
      } else {
        const statusText = typeof data?.status === "string" && data.status && data.status !== "UNKNOWN" ? `Status: ${data.status}` : "Not connected yet.";
        setGmailStatusMessage(statusText);
        try {
          localStorage.removeItem("gmail_connected");
          localStorage.removeItem("gmail_email");
        } catch {}
      }
    } catch (e: any) {
      setGmailConnected(false);
      setGmailProfile(null);
      setGmailEmail("");
      setGmailStatusMessage(e?.message || "Failed to check Gmail status");
    } finally {
      setIsRefreshingGmail(false);
    }
  }, [gmailConnId, readStoredConnectionRequestId, readStoredUserId]);

  const handleDisconnectGmail = useCallback(async () => {
    if (typeof window !== "undefined") {
      const proceed = window.confirm("Disconnect Gmail from OpenPoke?");
      if (!proceed) return;
    }

    try {
      setIsDisconnecting(true);
      setGmailStatusMessage("Disconnecting Gmail…");
      const userId = readStoredUserId();
      const connectionRequestId = readStoredConnectionRequestId();
      const resp = await fetch("/api/gmail/disconnect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ userId, connectionRequestId }),
      });
      const data = await resp.json().catch(() => ({}));

      if (!resp.ok || !data?.ok) {
        const message = data?.error || `Failed (${resp.status})`;
        setGmailStatusMessage(message);
        return;
      }

      setGmailConnected(false);
      setGmailEmail("");
      setGmailProfile(null);
      setGmailConnId("");
      setGmailStatusMessage("Gmail disconnected.");
      try {
        localStorage.removeItem("gmail_connected");
        localStorage.removeItem("gmail_email");
        localStorage.removeItem("gmail_connection_request_id");
        localStorage.removeItem("openpoke_user_id");
      } catch {}
    } catch (e: any) {
      setGmailStatusMessage(e?.message || "Failed to disconnect Gmail");
    } finally {
      setIsDisconnecting(false);
    }
  }, [readStoredConnectionRequestId, readStoredUserId]);

  useEffect(() => {
    setTimezone(settings.timezone);
    setAlerts({ chime: settings.chime, titleAlert: settings.titleAlert });
  }, [settings, open]);

  useEffect(() => {
    void refreshGmailStatus();
  }, [open, refreshGmailStatus]);

  const connectButtonLabel = connectingGmail ? "Opening…" : gmailConnected ? "Reconnect" : "Connect Gmail";
  const refreshButtonLabel = isRefreshingGmail ? "Refreshing…" : "Refresh status";
  const disconnectButtonLabel = isDisconnecting ? "Disconnecting…" : "Disconnect";

  return (
    <AnimatedDialog
      open={open}
      onClose={onClose}
      dismissible={!saving && !isClearingData}
      labelledBy="settings-title"
      panelClassName={`${styles.window} !max-w-3xl !overflow-hidden`}
    >
      <div className="mb-4 flex shrink-0 items-center justify-between">
        <h2
          id="settings-title"
          className="text-lg font-semibold"
        >
          Settings
        </h2>
        <button
          onClick={onClose}
          disabled={saving || isClearingData}
          className="rounded-md p-2 hover:bg-gray-100"
          aria-label="Close settings"
        >
          ✕
        </button>
      </div>
      <div className={styles.content}>
        <div
          role="tablist"
          aria-label="Settings sections"
          aria-orientation="vertical"
          className={styles.tabs}
        >
          {settingsTabs.map((tab, index) => (
            <button
              key={tab}
              id={`settings-tab-${tab.toLowerCase()}`}
              type="button"
              role="tab"
              aria-selected={activeTab === tab}
              aria-controls={`settings-panel-${tab.toLowerCase()}`}
              tabIndex={activeTab === tab ? 0 : -1}
              className={`rounded-lg px-2 py-2 text-left text-xs font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 sm:px-3 sm:text-sm ${activeTab === tab ? "bg-gray-100 text-gray-900" : "text-gray-500 hover:bg-gray-50 hover:text-gray-900"}`}
              onClick={() => setActiveTab(tab)}
              onKeyDown={(event) => {
                let nextIndex = index;
                if (event.key === "ArrowDown") nextIndex = (index + 1) % settingsTabs.length;
                else if (event.key === "ArrowUp") nextIndex = (index + settingsTabs.length - 1) % settingsTabs.length;
                else if (event.key === "Home") nextIndex = 0;
                else if (event.key === "End") nextIndex = settingsTabs.length - 1;
                else return;
                event.preventDefault();
                setActiveTab(settingsTabs[nextIndex]);
                event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[nextIndex]?.focus();
              }}
            >
              {tab}
            </button>
          ))}
        </div>
        <div className="min-h-0 min-w-0 flex-1 overflow-hidden">
          <div
            id="settings-panel-main"
            className={styles.tabPanel}
            role="tabpanel"
            aria-labelledby="settings-tab-main"
            hidden={activeTab !== "Main"}
            tabIndex={0}
          >
            <div>
              <label
                htmlFor="settings-timezone"
                className="mb-1 block text-sm font-medium text-gray-700"
              >
                Timezone
              </label>
              <input
                id="settings-timezone"
                className="input"
                type="text"
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                placeholder="e.g. America/New_York, Europe/London"
                readOnly={!timezone}
              />
              <p className="mt-1 text-xs text-gray-500">{timezone ? "Auto-detected from browser. Edit to override." : "Will be auto-detected on next page load."}</p>
            </div>
            <fieldset className="mt-6">
              <legend className="text-sm font-semibold text-gray-900">Background alerts</legend>
              <p className="mt-1 text-xs text-gray-500">Get alerted when a new reply arrives while you’re in another tab or app.</p>
              {(
                [
                  ["chime", "Play a chime"],
                  ["titleAlert", "Change tab title to “New message!”"],
                ] as const
              ).map(([key, label]) => (
                <label
                  key={key}
                  className={styles.alertOption}
                >
                  <span>{label}</span>
                  <input
                    type="checkbox"
                    role="switch"
                    className={styles.alertSwitch}
                    checked={alerts[key]}
                    onChange={(event) => setAlerts((previous) => ({ ...previous, [key]: event.target.checked }))}
                  />
                </label>
              ))}
            </fieldset>
            <div className="mt-8 rounded-xl border border-red-200 bg-red-50/40 p-4">
              <h3 className="text-sm font-semibold text-gray-900">Clear Data</h3>
              <p className="mt-2 text-sm text-gray-600">Permanently delete your conversation, agent history, reminders, and scheduled automations. This cannot be undone.</p>
              <button
                type="button"
                className="mt-4 inline-flex items-center gap-2 rounded-lg border border-red-200 bg-white px-3 py-2 text-sm font-medium text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
                onClick={handleClearData}
                disabled={saving || isClearingData || isChatBusy}
                aria-busy={isClearingData}
              >
                {isClearingData && <Spinner />}
                {isClearingData ? "Clearing…" : "Clear Data"}
              </button>
              {isChatBusy && <p className="mt-2 text-xs text-gray-500">You can clear data once the conversation finishes loading or your assistant finishes responding.</p>}
              {clearDataError && (
                <p
                  role="alert"
                  className="mt-3 text-sm text-red-700"
                >
                  {clearDataError}
                </p>
              )}
              {dataCleared && (
                <p
                  role="status"
                  className="mt-3 text-sm text-brand-700"
                >
                  Your conversation data has been cleared.
                </p>
              )}
            </div>
          </div>
          <div
            id="settings-panel-model"
            className={styles.tabPanel}
            role="tabpanel"
            aria-labelledby="settings-tab-model"
            hidden={activeTab !== "Model"}
            tabIndex={0}
          >
            <ModelSelector
              value={model}
              onChange={setModel}
              onLoad={loadModel}
              disabled={saving || isClearingData}
            />
          </div>
          <div
            id="settings-panel-integrations"
            className={styles.tabPanel}
            role="tabpanel"
            aria-labelledby="settings-tab-integrations"
            hidden={activeTab !== "Integrations"}
            tabIndex={0}
          >
            <h3 className="mb-3 text-sm font-semibold text-gray-900">Integrations</h3>
            <div className="rounded-xl border border-gray-200 bg-white/70 p-4 shadow-sm">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <div className="text-sm font-semibold text-gray-900">Gmail (via Composio)</div>
                  <p className="mt-1 text-sm text-gray-600">Connect Gmail to unlock email search, drafting, and automations inside OpenPoke.</p>
                </div>
                <span className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset ${isRefreshingGmail ? "bg-gray-50 text-gray-600 ring-gray-200" : gmailConnected ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : "bg-amber-50 text-amber-700 ring-amber-200"}`}>
                  {isRefreshingGmail && <Spinner />}
                  {isRefreshingGmail ? "Checking Gmail…" : gmailConnected ? "Connected" : "Not connected"}
                </span>
              </div>

              {gmailConnected ? (
                <div className="mt-4 space-y-3 rounded-lg border border-gray-100 bg-gray-50 p-3">
                  <div>
                    <div className="text-[11px] uppercase tracking-wide text-gray-500">Connected account</div>
                    <div className="mt-1 text-sm font-medium text-gray-900">{gmailEmail || "Email unavailable"}</div>
                  </div>
                  {gmailProfileDetails.length > 0 && (
                    <dl className="grid grid-cols-1 gap-2 text-xs text-gray-600 sm:grid-cols-3">
                      {gmailProfileDetails.map((item) => (
                        <div
                          key={item.label}
                          className="rounded-md border border-gray-200 bg-white/80 p-2"
                        >
                          <dt className="text-[10px] font-semibold uppercase tracking-wide text-gray-500">{item.label}</dt>
                          <dd className="mt-1 text-sm font-medium text-gray-900">{item.value}</dd>
                        </div>
                      ))}
                    </dl>
                  )}
                  {gmailStatusMessage && (
                    <p
                      className="text-xs text-gray-500"
                      aria-live="polite"
                    >
                      {gmailStatusMessage}
                    </p>
                  )}
                </div>
              ) : (
                <div
                  className="mt-4 rounded-lg border border-dashed border-gray-200 p-3 text-sm text-gray-500"
                  aria-live="polite"
                >
                  {isRefreshingGmail ? "Loading Gmail account details…" : gmailStatusMessage || "Complete the connection to view your Gmail account details here."}
                </div>
              )}

              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn gap-2"
                  onClick={handleConnectGmail}
                  disabled={connectingGmail || isRefreshingGmail || isDisconnecting}
                  aria-busy={connectingGmail}
                >
                  {connectingGmail && <Spinner className="rounded-full bg-white p-0.5" />}
                  {connectButtonLabel}
                </button>
                <button
                  type="button"
                  className="inline-flex items-center gap-2 rounded-md border border-gray-200 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
                  onClick={refreshGmailStatus}
                  disabled={isRefreshingGmail || connectingGmail || isDisconnecting}
                  aria-busy={isRefreshingGmail}
                >
                  {isRefreshingGmail && <Spinner />}
                  {refreshButtonLabel}
                </button>
                {gmailConnected && (
                  <button
                    type="button"
                    className="inline-flex items-center gap-2 rounded-md border border-transparent bg-red-50 px-3 py-2 text-sm font-medium text-red-600 hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-60"
                    onClick={handleDisconnectGmail}
                    disabled={isDisconnecting || connectingGmail || isRefreshingGmail}
                    aria-busy={isDisconnecting}
                  >
                    {isDisconnecting && <Spinner />}
                    {disconnectButtonLabel}
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      {saveError && (
        <p
          role="alert"
          className="mt-4 shrink-0 text-sm text-red-600"
        >
          {saveError}
        </p>
      )}
      <div className="mt-6 flex shrink-0 justify-end gap-2 border-t border-gray-200 pt-4">
        <button
          onClick={onClose}
          disabled={saving || isClearingData}
          className="rounded-md px-4 py-2 text-sm text-gray-700 hover:bg-gray-100"
        >
          Cancel
        </button>
        <button
          className="btn gap-2"
          onClick={saveSettings}
          disabled={saving || isClearingData}
          aria-busy={saving}
        >
          {saving && <Spinner className="rounded-full bg-white p-0.5" />}
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </AnimatedDialog>
  );
}
