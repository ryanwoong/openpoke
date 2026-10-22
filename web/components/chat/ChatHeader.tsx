import { Spinner } from "../ui/LoadingIndicator";

interface ChatHeaderProps {
  onOpenSettings: () => void;
  onClearHistory: () => void;
  isClearingHistory: boolean;
  isLoadingHistory: boolean;
}

export function ChatHeader({ onOpenSettings, onClearHistory, isClearingHistory, isLoadingHistory }: ChatHeaderProps) {
  return (
    <header className="mb-4 flex items-center justify-between">
      <div className="flex items-center">
        <h1 className="text-lg font-semibold">OpenPoke 🌴</h1>
      </div>
      <div className="flex items-center gap-2">
        <button
          className="rounded-md border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50"
          onClick={onOpenSettings}
        >
          Settings
        </button>
        <button
          className="inline-flex items-center gap-2 rounded-md border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
          onClick={onClearHistory}
          disabled={isClearingHistory || isLoadingHistory}
          aria-busy={isClearingHistory}
        >
          {isClearingHistory && <Spinner />}
          {isClearingHistory ? "Clearing…" : "Clear"}
        </button>
      </div>
    </header>
  );
}
