import React, { useState, useMemo } from "react";
import {
  MessageSquare,
  Plus,
  Trash2,
  Edit2,
  Check,
  X,
  Search,
  ChevronLeft,
  ChevronRight,
  Sparkles,
} from "lucide-react";
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
      (c) =>
        c.title.toLowerCase().includes(q) ||
        (c.last_message && c.last_message.toLowerCase().includes(q))
    );
  }, [conversations, searchQuery]);

  // Group conversations by date
  const groupedConversations = useMemo(() => {
    const now = Date.now() / 1000;
    const daySec = 86400;

    const groups: { [key: string]: ConversationSummary[] } = {
      Today: [],
      Yesterday: [],
      "Previous 7 Days": [],
      Older: [],
    };

    filteredConversations.forEach((conv) => {
      const age = now - conv.updated_at;
      if (age < daySec) {
        groups.Today.push(conv);
      } else if (age < daySec * 2) {
        groups.Yesterday.push(conv);
      } else if (age < daySec * 7) {
        groups["Previous 7 Days"].push(conv);
      } else {
        groups.Older.push(conv);
      }
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
    if (editTitle.trim()) {
      onRenameConversation(id, editTitle.trim());
    }
    setEditingId(null);
  };

  const cancelRename = (e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingId(null);
  };

  const handleDelete = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (window.confirm("Delete this conversation thread?")) {
      onDeleteConversation(id);
    }
  };

  if (isCollapsed) {
    return (
      <aside className="w-16 bg-[#07090E] border-r border-white/[0.08] flex flex-col items-center py-4 select-none shrink-0 transition-all duration-200">
        <button
          onClick={onToggleCollapse}
          className="p-2 rounded-xl text-slate-400 hover:text-white hover:bg-white/[0.06] transition mb-4"
          title="Expand sidebar"
        >
          <ChevronRight className="w-5 h-5" />
        </button>

        <button
          onClick={onNewConversation}
          className="p-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white shadow-indigo-glow transition mb-6"
          title="New Chat"
        >
          <Plus className="w-5 h-5" />
        </button>

        <div className="flex-1 overflow-y-auto space-y-3 w-full flex flex-col items-center px-2">
          {conversations.slice(0, 10).map((c) => (
            <button
              key={c.id}
              onClick={() => onSelectConversation(c.id)}
              className={`p-2 rounded-xl text-xs transition relative group ${
                activeConversationId === c.id
                  ? "bg-indigo-600/20 text-indigo-300 border border-indigo-500/40"
                  : "text-slate-400 hover:text-white hover:bg-white/[0.04]"
              }`}
              title={c.title}
            >
              <MessageSquare className="w-4 h-4" />
            </button>
          ))}
        </div>
      </aside>
    );
  }

  return (
    <aside className="w-72 bg-[#07090E] border-r border-white/[0.08] flex flex-col select-none shrink-0 transition-all duration-200">
      {/* Top Header & App Brand */}
      <div className="p-3.5 border-b border-white/[0.06] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-indigo-600/20 border border-indigo-500/30 flex items-center justify-center text-indigo-400">
            <Sparkles className="w-4 h-4" />
          </div>
          <div>
            <h1 className="text-sm font-semibold text-white tracking-wide">Mustang AI</h1>
            <p className="text-[10px] text-slate-400 font-mono">RTX 5050 • Local</p>
          </div>
        </div>

        <button
          onClick={onToggleCollapse}
          className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.06] transition"
          title="Collapse sidebar"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
      </div>

      {/* New Chat Button */}
      <div className="p-3">
        <button
          onClick={onNewConversation}
          className="w-full flex items-center justify-center gap-2 py-2 px-3 rounded-xl bg-gradient-to-r from-indigo-600 to-indigo-700 hover:from-indigo-500 hover:to-indigo-600 text-white font-medium text-xs shadow-indigo-glow transition"
        >
          <Plus className="w-4 h-4" />
          <span>New Chat</span>
        </button>
      </div>

      {/* Search Input */}
      <div className="px-3 pb-2">
        <div className="relative">
          <Search className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-slate-400" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search conversations..."
            className="w-full bg-[#0D1220] border border-white/[0.08] rounded-lg pl-8 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500/60"
          />
        </div>
      </div>

      {/* Conversation Thread List */}
      <div className="flex-1 overflow-y-auto px-2 space-y-4 py-2">
        {Object.entries(groupedConversations).map(([groupName, items]) => {
          if (items.length === 0) return null;
          return (
            <div key={groupName} className="space-y-1">
              <div className="px-2 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                {groupName}
              </div>
              {items.map((conv) => {
                const isActive = activeConversationId === conv.id;
                const isEditing = editingId === conv.id;

                if (isEditing) {
                  return (
                    <form
                      key={conv.id}
                      onSubmit={(e) => saveRename(conv.id, e)}
                      className="flex items-center gap-1 px-2 py-1.5 rounded-lg bg-[#0F1424] border border-indigo-500/50"
                    >
                      <input
                        type="text"
                        autoFocus
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        className="flex-1 bg-transparent text-xs text-white focus:outline-none"
                      />
                      <button
                        type="submit"
                        className="p-1 hover:text-emerald-400 text-slate-400"
                        title="Save"
                      >
                        <Check className="w-3.5 h-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={cancelRename}
                        className="p-1 hover:text-rose-400 text-slate-400"
                        title="Cancel"
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
                    className={`group relative flex items-center justify-between px-2.5 py-2 rounded-xl text-xs cursor-pointer transition ${
                      isActive
                        ? "bg-indigo-600/20 text-white font-medium border border-indigo-500/40 shadow-subtle"
                        : "text-slate-300 hover:text-white hover:bg-white/[0.04]"
                    }`}
                  >
                    <div className="flex items-center gap-2 overflow-hidden flex-1 mr-2">
                      <MessageSquare
                        className={`w-3.5 h-3.5 shrink-0 ${
                          isActive ? "text-indigo-400" : "text-slate-400"
                        }`}
                      />
                      <span className="truncate">{conv.title}</span>
                    </div>

                    {/* Action buttons on hover */}
                    <div className="opacity-0 group-hover:opacity-100 flex items-center gap-1 transition">
                      <button
                        onClick={(e) => startRename(conv, e)}
                        className="p-1 rounded text-slate-400 hover:text-white hover:bg-white/10"
                        title="Rename"
                      >
                        <Edit2 className="w-3 h-3" />
                      </button>
                      <button
                        onClick={(e) => handleDelete(conv.id, e)}
                        className="p-1 rounded text-slate-400 hover:text-rose-400 hover:bg-white/10"
                        title="Delete"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          );
        })}

        {conversations.length === 0 && (
          <div className="text-center py-8 text-xs text-slate-400">
            No conversations yet. Start a new chat!
          </div>
        )}
      </div>

      {/* Footer Info */}
      <div className="p-3 border-t border-white/[0.06] text-[11px] text-slate-400 flex items-center justify-between">
        <span>Mustang AI v0.1.0</span>
        <span className="text-emerald-400 font-mono">● Local Engine</span>
      </div>
    </aside>
  );
};

