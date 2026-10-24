export type ExecutionSpan = {
  id: string;
  run_id: string;
  parent_id: string | null;
  kind: string;
  label: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  input: unknown;
  output: unknown;
};

export type GraphNode = {
  id: string;
  kind: string;
  label: string;
  description: string;
  status: string;
  meta: string;
  x: number;
  y: number;
  height: number;
  spanIds: string[];
  updatedAt?: string;
};
export type GraphEdge = { source: string; target: string; active: boolean };
export type ExecutionGraph = { nodes: GraphNode[]; edges: GraphEdge[] };
export type ExecutionTask = { id: string; title: string; spans: ExecutionSpan[]; status: string; started_at: string; ended_at: string | null; occurrences: number };

function inputText(span: ExecutionSpan, key: string) {
  if (!span.input || typeof span.input !== "object") return "";
  const value = (span.input as Record<string, unknown>)[key];
  return typeof value === "string" ? value : "";
}

export function activityLabel(span: ExecutionSpan) {
  if (span.kind === "interaction") return inputText(span, "prompt") || (inputText(span, "agent_message") ? "Preparing the reply" : "Interaction agent");
  if (span.kind === "execution") return span.label;
  if (span.kind === "model") return span.label.startsWith("Context compaction") ? "Compacting results" : "Requesting a model response";
  const labels: Record<string, string> = { GMAIL_FETCH_EMAILS: "Checking inbox", GMAIL_GET_PROFILE: "Checking Gmail connection", send_message_to_agent: "Delegating work", send_message_to_user: "Delivering a reply", wait: "Waiting for delegated work" };
  const operation = span.label.replace(/^Gmail · /, "");
  return labels[operation] || operation.replace(/[_-]/g, " ").replace(/^gmail /i, "Gmail · ");
}

export function taskStatus(spans: ExecutionSpan[]) {
  if (spans.some((span) => span.status === "running")) return "running";
  if (spans.some((span) => span.status === "failed" || span.status === "cancelled")) return "failed";
  return "completed";
}

export function executionHistory(spans: ExecutionSpan[], background = false): ExecutionTask[] {
  const groups = new Map<string, ExecutionSpan[]>();
  for (const span of spans) {
    const group = groups.get(span.run_id) || [];
    group.push(span);
    groups.set(span.run_id, group);
  }
  const tasks: ExecutionTask[] = [];
  const recurring = new Map<string, ExecutionTask>();
  for (const root of spans.filter((span) => !span.parent_id).reverse()) {
    const prompt = root.kind === "interaction" ? inputText(root, "prompt") : "";
    if (background ? Boolean(prompt) : !prompt) continue;
    const entries = groups.get(root.run_id) || [root];
    const status = taskStatus(entries);
    const ended =
      entries
        .map((span) => span.ended_at)
        .filter((value): value is string => Boolean(value))
        .sort()
        .at(-1) || null;
    const task = { id: root.id, title: prompt || activityLabel(root), spans: entries, status, started_at: root.started_at, ended_at: status === "running" ? null : ended, occurrences: 1 };
    if (background) {
      const key = `${root.kind}:${root.label}`;
      const existing = recurring.get(key);
      if (existing) {
        existing.occurrences++;
        if (status === "running" && existing.status !== "running") {
          Object.assign(existing, task, { occurrences: existing.occurrences });
        }
        continue;
      }
      recurring.set(key, task);
    }
    tasks.push(task);
  }
  return tasks;
}

function since(span: ExecutionSpan | undefined, now: number) {
  if (!span) return "Ready";
  const seconds = Math.max(0, Math.floor((now - Date.parse(span.ended_at || span.started_at)) / 1000));
  return seconds < 5 ? "Just now" : seconds < 60 ? `${seconds}s ago` : seconds < 3600 ? `${Math.floor(seconds / 60)}m ago` : `${Math.floor(seconds / 3600)}h ago`;
}

export function liveExecutionGraph(spans: ExecutionSpan[], now = Date.now()): ExecutionGraph {
  const workers = new Map<string, ExecutionSpan>();
  for (const span of spans) {
    if (span.kind !== "execution") continue;
    workers.delete(span.label);
    workers.set(span.label, span);
  }
  const activeNames = new Set(spans.filter((span) => span.kind === "execution" && span.status === "running").map((span) => span.label));
  const names = Array.from(workers.keys()).filter((name) => activeNames.has(name));
  for (const name of Array.from(workers.keys()).reverse()) if (!names.includes(name) && names.length < activeNames.size + 4) names.push(name);
  names.sort();
  const shownNames = new Set(names);
  function nodeId(span: ExecutionSpan) {
    if (span.kind === "interaction") return "interaction";
    if (span.kind === "execution") return shownNames.has(span.label) ? `worker:${span.label}` : "execution";
    if (span.kind === "model") return "models";
    if (span.kind === "access" && span.label.startsWith("Gmail ·")) return "gmail";
    if (span.label === "send_message_to_agent") return "execution";
    if (["send_message_to_user", "send_draft", "wait"].includes(span.label)) return "interaction";
    return "tools";
  }
  const mapped = new Map<string, ExecutionSpan[]>();
  for (const span of spans) {
    const id = nodeId(span);
    const entries = mapped.get(id) || [];
    entries.push(span);
    mapped.set(id, entries);
  }
  const definitions = [
    { id: "interaction", kind: "interaction", label: "Interaction agent", description: "Receives prompts and delivers replies.", x: 28, y: 260 },
    { id: "execution", kind: "execution", label: "Execution agents", description: "Delegates tasks to named workers.", x: 368, y: 260 },
    ...names.map((name, index) => ({ id: `worker:${name}`, kind: "execution", label: name, description: "Ready for delegated work.", x: 368, y: 500 + index * 220 })),
    { id: "gmail", kind: "access", label: "Gmail", description: "Inbox checks and email operations.", x: 748, y: 30 },
    { id: "models", kind: "model", label: "Model provider", description: "Model requests through OpenRouter.", x: 748, y: 260 },
    { id: "tools", kind: "tool", label: "Tools & workflows", description: "Task tools, schedules, and integrations.", x: 748, y: 490 },
  ];
  const nodes = definitions.map((definition): GraphNode => {
    const entries = definition.id === "execution" ? spans.filter((span) => span.kind === "execution" || span.label === "send_message_to_agent") : mapped.get(definition.id) || [];
    const active = definition.id === "execution" ? spans.filter((span) => span.kind === "execution" && span.status === "running") : entries.filter((span) => span.status === "running");
    const latest = entries.at(-1);
    const current = active.at(-1);
    return { ...definition, description: current ? inputText(current, "instructions") || activityLabel(current) : definition.description, status: current ? "running" : latest?.status === "failed" || latest?.status === "cancelled" ? "failed" : "idle", meta: active.length ? `${active.length} active` : latest ? `Last activity · ${since(latest, now)}` : "Ready", height: 172, updatedAt: latest?.ended_at || latest?.started_at, spanIds: entries.map((span) => span.id) };
  });
  const edges = new Map<string, GraphEdge>();
  function connect(source: string, target: string, active = false) {
    if (source === target) return;
    const key = `${source}->${target}`;
    edges.set(key, { source, target, active: active || Boolean(edges.get(key)?.active) });
  }
  connect("interaction", "execution");
  for (const service of ["gmail", "models", "tools"]) connect("execution", service);
  for (const name of names) connect("execution", `worker:${name}`, activeNames.has(name));
  const byId = new Map(spans.map((span) => [span.id, span]));
  const visited = new Set<string>();
  for (const active of spans.filter((span) => span.status === "running")) {
    let child: ExecutionSpan | undefined = active;
    while (child?.parent_id && !visited.has(child.id)) {
      visited.add(child.id);
      const parent = byId.get(child.parent_id);
      if (!parent) break;
      connect(nodeId(parent), nodeId(child), true);
      child = parent;
    }
  }
  return { nodes, edges: Array.from(edges.values()) };
}

export function recordedExecutionGraph(spans: ExecutionSpan[]): ExecutionGraph {
  const depths = new Map<string, number>();
  const nodes = spans.map((span, index): GraphNode => {
    const depth = span.parent_id ? (depths.get(span.parent_id) ?? -1) + 1 : 0;
    depths.set(span.id, depth);
    return { id: span.id, kind: span.kind, label: activityLabel(span), description: span.label, status: span.status, meta: span.kind, x: 28 + depth * 280, y: 30 + index * 180, height: 148, spanIds: [span.id] };
  });
  const ids = new Set(nodes.map((node) => node.id));
  return { nodes, edges: spans.filter((span) => span.parent_id && ids.has(span.parent_id)).map((span) => ({ source: span.parent_id!, target: span.id, active: false })) };
}

type Point = { x: number; y: number };
export type EdgeRoute = { path: string; start: Point; end: Point; points: Point[] };

function roundedPath(points: Point[]) {
  let path = `M ${points[0].x} ${points[0].y}`;
  for (let index = 1; index < points.length - 1; index++) {
    const previous = points[index - 1];
    const corner = points[index];
    const next = points[index + 1];
    const incoming = Math.hypot(corner.x - previous.x, corner.y - previous.y);
    const outgoing = Math.hypot(next.x - corner.x, next.y - corner.y);
    if (!incoming || !outgoing) continue;
    const radius = Math.min(10, incoming / 2, outgoing / 2);
    const entry = { x: corner.x - ((corner.x - previous.x) / incoming) * radius, y: corner.y - ((corner.y - previous.y) / incoming) * radius };
    const exit = { x: corner.x + ((next.x - corner.x) / outgoing) * radius, y: corner.y + ((next.y - corner.y) / outgoing) * radius };
    path += ` L ${entry.x} ${entry.y} Q ${corner.x} ${corner.y} ${exit.x} ${exit.y}`;
  }
  const end = points[points.length - 1];
  return `${path} L ${end.x} ${end.y}`;
}

export function routeEdge(source: GraphNode, target: GraphNode): EdgeRoute {
  let start: Point;
  let end: Point;
  let points: Point[];
  if (source.x === target.x) {
    if (Math.abs(source.y - target.y) > 250) {
      start = { x: source.x + 245, y: source.y + source.height / 2 };
      end = { x: target.x + 245, y: target.y + target.height / 2 };
      const lane = source.x + 282;
      points = [start, { x: lane, y: start.y }, { x: lane, y: end.y }, { x: end.x + 6, y: end.y }];
    } else {
      const down = target.y > source.y;
      start = { x: source.x + 120, y: source.y + (down ? source.height + 5 : -5) };
      end = { x: target.x + 120, y: target.y + (down ? -5 : target.height + 5) };
      points = [start, { x: end.x, y: end.y + (down ? -6 : 6) }];
    }
  } else if (Math.abs(target.x - source.x) > 400) {
    const above = target.y <= source.y;
    start = { x: source.x + 120, y: source.y + (above ? -5 : source.height + 5) };
    end = { x: target.x + 120, y: target.y + (above ? -5 : target.height + 5) };
    const lane = above ? Math.min(source.y, target.y) - 28 : Math.max(source.y + source.height, target.y + target.height) + 28;
    points = [start, { x: start.x, y: lane }, { x: end.x, y: lane }, { x: end.x, y: end.y + (above ? -6 : 6) }];
  } else {
    const forward = target.x > source.x;
    start = { x: source.x + (forward ? 245 : -5), y: source.y + source.height / 2 };
    end = { x: target.x + (forward ? -5 : 245), y: target.y + target.height / 2 };
    const lane = (start.x + end.x) / 2;
    const tip = { x: end.x + (forward ? -6 : 6), y: end.y };
    points = start.y === end.y ? [start, tip] : [start, { x: lane, y: start.y }, { x: lane, y: end.y }, tip];
  }
  return { path: roundedPath(points), start, end, points };
}
