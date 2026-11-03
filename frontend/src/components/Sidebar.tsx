import React, { useState, useMemo } from "react";
import { Plus, Trash2, Pencil, Check, X, Search, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { ConversationSummary } from "../types";

interface SidebarProps {
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  onRenameConversation: (id: string, newTitle: string) => void;
  onDeleteConversation: (id: string) => void;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
}

const iconButton =
  "h-7 w-7 flex items-center justify-center rounded-md text-muted hover:text-fg hover:bg-raised transition-colors";

export const Sidebar: React.FC<SidebarProps> = ({
  conversations,
  activeConversationId,
  onSelectConversation,
  onNewConversation,
  onRenameConversation,
  onDeleteConversation,
  isCollapsed,
  onToggleCollapse,
}) => {
  const [searchQuery, setSearchQuery] = useState("");
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
    const groups: Record<string, ConversationSummary[]> = {
      Today: [],
      Yesterday: [],
      "Previous 7 days": [],
      Older: [],
    };
    filteredConversations.forEach((conv) => {
      const age = now - conv.updated_at;
      if (age < day) groups.Today.push(conv);
      else if (age < day * 2) groups.Yesterday.push(conv);
      else if (age < day * 7) groups["Previous 7 days"].push(conv);
      else groups.Older.push(conv);
    });
    return groups;
  }, [filteredConversations]);

  const startRename = (conv: ConversationSummary, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingId(conv.id);
    setEditTitle(conv.title);
  };

  const saveRename = (id: string, e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (editTitle.trim()) onRenameConversation(id, editTitle.trim());
    setEditingId(null);
  };

  const handleDelete = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (window.confirm("Delete this conversation?")) onDeleteConversation(id);
  };

  if (isCollapsed) {
    return (
      <aside className="w-12 shrink-0 bg-panel border-r border-line flex flex-col items-center gap-1 py-2 select-none">
        <button onClick={onToggleCollapse} className={iconButton} title="Show sidebar" aria-label="Show sidebar">
          <PanelLeftOpen className="w-4 h-4" />
        </button>
        <button onClick={onNewConversation} className={iconButton} title="New chat" aria-label="New chat">
          <Plus className="w-4 h-4" />
        </button>
      </aside>
    );
  }

  return (
    <aside className="w-64 shrink-0 bg-panel border-r border-line flex flex-col select-none">
      <div className="h-11 shrink-0 px-3 flex items-center justify-between border-b border-line">
        <span className="text-[13px] font-semibold text-fg">Mustang AI</span>
        <button onClick={onToggleCollapse} className={iconButton} title="Hide sidebar" aria-label="Hide sidebar">
          <PanelLeftClose className="w-4 h-4" />
        </button>
      </div>

      <div className="p-2 space-y-2">
        <button
          onClick={onNewConversation}
          className="w-full h-8 flex items-center gap-2 px-2.5 rounded-md border border-line text-[13px] text-fg hover:bg-raised transition-colors"
        >
          <Plus className="w-4 h-4 text-muted" />
          New chat
        </button>
        <div className="relative">
          <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-subtle" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search chats"
            className="w-full h-8 bg-canvas border border-line rounded-md pl-8 pr-2 text-[13px] text-fg placeholder:text-subtle focus:outline-none focus:border-accent"
          />
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 pb-2">
        {Object.entries(groupedConversations).map(([groupName, items]) =>
          items.length === 0 ? null : (
            <div key={groupName} className="mt-3 first:mt-1">
              <div className="px-2 pb-1 text-xs text-subtle">{groupName}</div>
              {items.map((conv) => {
                const isActive = activeConversationId === conv.id;

                if (editingId === conv.id) {
                  return (
                    <form
                      key={conv.id}
                      onSubmit={(e) => saveRename(conv.id, e)}
                      className="flex items-center gap-1 h-8 px-1.5 rounded-md border border-accent bg-canvas"
                    >
                      <input
                        type="text"
                        autoFocus
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        onKeyDown={(e) => e.key === "Escape" && setEditingId(null)}
                        className="flex-1 min-w-0 bg-transparent text-[13px] text-fg focus:outline-none"
                      />
                      <button type="submit" className="p-1 text-muted hover:text-fg" title="Save" aria-label="Save">
                        <Check className="w-3.5 h-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditingId(null)}
                        className="p-1 text-muted hover:text-fg"
                        title="Cancel"
                        aria-label="Cancel"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </form>
                  );
                }

                return (
                  <div
                    key={conv.id}
                    onClick={() => onSelectConversation(conv.id)}
                    className={`group flex items-center h-8 px-2 rounded-md cursor-pointer text-[13px] transition-colors ${
                      isActive ? "bg-raised text-fg" : "text-muted hover:bg-raised hover:text-fg"
                    }`}
                  >
                    <span className="truncate flex-1">{conv.title}</span>
                    <div className="hidden group-hover:flex items-center">
                      <button
                        onClick={(e) => startRename(conv, e)}
                        className="p-1 rounded text-subtle hover:text-fg"
                        title="Rename"
                        aria-label="Rename"
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={(e) => handleDelete(conv.id, e)}
                        className="p-1 rounded text-subtle hover:text-danger"
                        title="Delete"
                        aria-label="Delete"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )
        )}

        {conversations.length === 0 && <p className="px-2 py-6 text-[13px] text-subtle">No chats yet.</p>}
        {conversations.length > 0 && filteredConversations.length === 0 && (
          <p className="px-2 py-6 text-[13px] text-subtle">No chats match “{searchQuery}”.</p>
        )}
      </nav>

      <div className="h-8 shrink-0 px-3 flex items-center border-t border-line text-xs text-subtle">v0.1.0</div>
    </aside>
  );
};
