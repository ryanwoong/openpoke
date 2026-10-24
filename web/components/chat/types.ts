export interface ChatBubble {
  id: string;
  role: string;
  text: string;
  timestamp?: string | null;
  source?: "scheduled" | "failure" | null;
  requestId?: string | null;
  retryable?: boolean;
}
