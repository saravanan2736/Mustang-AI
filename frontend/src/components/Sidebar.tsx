import React, { useState, useMemo } from "react";
import {
  Check,
  Cpu,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Search,
  SquarePen,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import { ConversationSummary, EngineStatus, HardwareTelemetry } from "../types";

interface SidebarProps {
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  onRenameConversation: (id: string, newTitle: string) => void;
  onDeleteConversation: (id: string) => void;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  telemetry: HardwareTelemetry | null;
  engineStatus: EngineStatus | null;
  tokensPerSec: number;
}

const iconButton =
  "h-8 w-8 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-raised/80 transition-colors";
const gb = (mb: number) => (mb / 1024).toFixed(1);

const Logo = () => (
  <span className="w-7 h-7 rounded-lg bg-gradient-to-br from-accent to-purple-700 text-white flex items-center justify-center shrink-0" aria-hidden>
    <Zap className="w-4 h-4 fill-current" />
  </span>
);

export const Sidebar: React.FC<SidebarProps> = ({
  conversations,
  activeConversationId,
  onSelectConversation,
  onNewConversation,
  onRenameConversation,
  onDeleteConversation,
  isCollapsed,
  onToggleCollapse,
  telemetry,
  engineStatus,
  tokensPerSec,
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");

  const filteredConversations = useMemo(() => {
    if (!searchQuery.trim()) return conversations;
    const q = searchQuery.toLowerCase();
    return conversations.filter(
      (c) => c.title.toLowerCase().includes(q) || (c.last_message && c.last_message.toLowerCase().includes(q))
    );
  }, [conversations, searchQuery]);

  const groupedConversations = useMemo(() => {
    const now = Date.now() / 1000;
    const day = 86400;
    const groups: Record<string, ConversationSummary[]> = { Today: [], Yesterday: [], "Previous 7 days": [], Older: [] };
    filteredConversations.forEach((conv) => {
      const age = now - conv.updated_at;
      if (age < day) groups.Today.push(conv);
      else if (age < day * 2) groups.Yesterday.push(conv);
      else if (age < day * 7) groups["Previous 7 days"].push(conv);
      else groups.Older.push(conv);
    });
    return groups;
  }, [filteredConversations]);

  const saveRename = (id: string, e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (editTitle.trim()) onRenameConversation(id, editTitle.trim());
    setEditingId(null);
  };

  if (isCollapsed) {
    return (
      <aside className="w-14 shrink-0 bg-surface border-r border-line/50 flex flex-col items-center gap-2 py-3 select-none transition-all duration-200">
        <Logo />
        <button onClick={onToggleCollapse} className={iconButton} title="Show sidebar" aria-label="Show sidebar">
          <PanelLeftOpen className="w-4 h-4" />
        </button>
        <button onClick={onNewConversation} className={`${iconButton} hover:text-accent`} title="New chat" aria-label="New chat">
          <SquarePen className="w-4 h-4" />
        </button>
      </aside>
    );
  }

  const gpu = telemetry?.gpu;
  const mem = telemetry?.memory;
  const loaded = engineStatus?.state === "READY" || engineStatus?.state === "GENERATING";

  return (
    <aside className="w-64 shrink-0 bg-surface border-r border-line/50 flex flex-col select-none transition-all duration-200">
      <div className="h-14 shrink-0 px-3.5 flex items-center gap-2.5 border-b border-line/30">
        <Logo />
        <span className="text-[15px] font-semibold tracking-tight text-fg flex-1">Mustang AI</span>
        <button onClick={onToggleCollapse} className={iconButton} title="Hide sidebar" aria-label="Hide sidebar">
          <PanelLeftClose className="w-4 h-4" />
        </button>
      </div>

      <div className="px-3 pt-3">
        <button
          onClick={onNewConversation}
          className="w-full h-9 flex items-center gap-2 px-3 rounded-lg bg-panel/80 border border-line/60 text-[13px] font-medium text-fg hover:bg-raised/80 hover:border-accent/40 shadow-sm transition-all duration-150"
        >
          <SquarePen className="w-4 h-4 text-accent" />
          <span>New Chat</span>
        </button>
      </div>

      <div className="mt-4 px-3.5 flex items-center justify-between">
        <span className="text-[12px] font-medium uppercase tracking-wider text-subtle">Chats</span>
        <button
          onClick={() => {
            setSearchOpen(!searchOpen);
            if (searchOpen) setSearchQuery("");
          }}
          className="h-6 w-6 flex items-center justify-center rounded-md text-subtle hover:text-fg hover:bg-raised/70 transition-colors"
          title="Search chats"
          aria-label="Search chats"
        >
          {searchOpen ? <X className="w-3.5 h-3.5" /> : <Search className="w-3.5 h-3.5" />}
        </button>
      </div>
      {searchOpen && (
        <div className="px-3 pt-1.5">
          <input
            type="text"
            autoFocus
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search chats..."
            className="w-full h-8 bg-panel/90 border border-line/80 rounded-lg px-2.5 text-[13px] text-fg placeholder:text-subtle/80 focus:outline-none focus:border-accent/70 focus:ring-1 focus:ring-accent/30 transition-all"
          />
        </div>
      )}

      <nav className="flex-1 overflow-y-auto px-2.5 py-1.5 space-y-3">
        {Object.entries(groupedConversations).map(([groupName, items]) =>
          items.length === 0 ? null : (
            <div key={groupName} className="space-y-0.5">
              <div className="px-2 py-1 text-[11px] font-medium uppercase tracking-wider text-subtle/80">{groupName}</div>
              {items.map((conv) => {
                const isActive = activeConversationId === conv.id;

                if (editingId === conv.id) {
                  return (
                    <form
                      key={conv.id}
                      onSubmit={(e) => saveRename(conv.id, e)}
                      className="flex items-center gap-1 h-8 px-2 rounded-lg border border-accent/80 bg-panel/90"
                    >
                      <input
                        type="text"
                        autoFocus
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        onKeyDown={(e) => e.key === "Escape" && setEditingId(null)}
                        className="flex-1 min-w-0 bg-transparent text-[13px] text-fg focus:outline-none"
                      />
                      <button type="submit" className="p-1 text-accent hover:text-fg" title="Save" aria-label="Save">
                        <Check className="w-3.5 h-3.5" />
                      </button>
                    </form>
                  );
                }

                return (
                  <div
                    key={conv.id}
                    onClick={() => onSelectConversation(conv.id)}
                    className={`group relative flex items-center gap-2.5 h-8 px-2.5 rounded-lg cursor-pointer text-[13px] transition-all duration-150 ${isActive
                        ? "bg-raised/90 text-fg font-medium border border-line/50 shadow-sm before:absolute before:left-0 before:top-1.5 before:bottom-1.5 before:w-1 before:rounded-r before:bg-accent"
                        : "text-muted hover:bg-raised/50 hover:text-fg border border-transparent"
                      }`}
                  >
                    <MessageSquare className={`w-3.5 h-3.5 shrink-0 ${isActive ? "text-accent" : "text-subtle"}`} />
                    <span className="truncate flex-1">{conv.title}</span>
                    <div className="hidden group-hover:flex items-center gap-0.5">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditingId(conv.id);
                          setEditTitle(conv.title);
                        }}
                        className="p-1 rounded text-subtle hover:text-fg hover:bg-panel"
                        title="Rename"
                        aria-label="Rename"
                      >
                        <Pencil className="w-3 h-3" />
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          if (window.confirm("Delete this conversation?")) onDeleteConversation(conv.id);
                        }}
                        className="p-1 rounded text-subtle hover:text-danger hover:bg-panel"
                        title="Delete"
                        aria-label="Delete"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )
        )}
        {conversations.length === 0 && <p className="px-2 py-3 text-[13px] text-subtle">No chats yet.</p>}
        {conversations.length > 0 && filteredConversations.length === 0 && (
          <p className="px-2 py-3 text-[13px] text-subtle">No chats match “{searchQuery}”.</p>
        )}
      </nav>

      <div className="p-3 border-t border-line/30">
        <div className="rounded-xl bg-panel/70 border border-line/60 p-3 shadow-sm">
          <div className="flex items-center gap-2 mb-2">
            <span className="w-7 h-7 flex items-center justify-center rounded-lg bg-raised/80 border border-line/60 text-accent">
              <Cpu className="w-4 h-4" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-medium text-fg flex items-center justify-between">
                <span>This PC</span>
                <span className={`w-1.5 h-1.5 rounded-full ${loaded ? "bg-success shadow-[0_0_6px_#34d399]" : "bg-subtle"}`} />
              </div>
              <div className="text-[11px] text-subtle truncate" title={engineStatus?.model_name ?? undefined}>
                {loaded ? engineStatus?.model_name?.replace(/\.gguf$/i, "") : "No model loaded"}
              </div>
            </div>
          </div>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px] tabular-nums pt-1 border-t border-line/40">
            <dt className="text-subtle font-medium">GPU VRAM</dt>
            <dd className="text-muted text-right truncate" title={gpu?.name}>
              {gpu?.available ? `${gb(gpu.vram_used_mb)} / ${gb(gpu.vram_total_mb)} GB` : "none"}
            </dd>
            <dt className="text-subtle font-medium">System RAM</dt>
            <dd className="text-muted text-right">{mem ? `${gb(mem.ram_used_mb)} / ${gb(mem.ram_total_mb)} GB` : "—"}</dd>
            <dt className="text-subtle font-medium">Throughput</dt>
            <dd className="text-accent font-medium text-right">{tokensPerSec > 0 ? `${tokensPerSec} tok/s` : "—"}</dd>
          </dl>
        </div>
      </div>
    </aside>
  );
};
