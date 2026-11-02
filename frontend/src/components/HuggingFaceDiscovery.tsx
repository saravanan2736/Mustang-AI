import React, { useState, useEffect, useCallback } from "react";
import {
  Search,
  Download,
  Heart,
  ExternalLink,
  Layers,
  FileCheck,
  ChevronLeft,
  RefreshCw,
  AlertTriangle,
  FileText,
  ShieldCheck,
  Filter,
} from "lucide-react";
import {
  DownloadTask,
  HFSearchResultItem,
  HFRepositoryDetailResponse,
  HFGGUFFileItem,
  PreparedDownloadPreview,
} from "../types";
import {
  searchHFModels,
  fetchHFRepositoryDetails,
  createDownload,
} from "../services/api";

interface HuggingFaceDiscoveryProps {
  onFilePrepared?: (preview: PreparedDownloadPreview) => void;
  onDownloadStarted?: (task: DownloadTask) => void;
}

export const HuggingFaceDiscovery: React.FC<HuggingFaceDiscoveryProps> = ({
  onFilePrepared,
  onDownloadStarted,
}) => {
  // Search state
  const [query, setQuery] = useState("qwen2.5");
  const [searchInput, setSearchInput] = useState("qwen2.5");
  const [sortBy, setSortBy] = useState<string>("downloads");
  const [searchResults, setSearchResults] = useState<HFSearchResultItem[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  // Selected repository details state
  const [selectedRepoId, setSelectedRepoId] = useState<string | null>(null);
  const [repoDetails, setRepoDetails] = useState<HFRepositoryDetailResponse | null>(null);
  const [isLoadingRepo, setIsLoadingRepo] = useState(false);
  const [repoError, setRepoError] = useState<string | null>(null);
  const [showModelCard, setShowModelCard] = useState(false);

  // Quantization filter inside repo details
  const [selectedQuantFilter, setSelectedQuantFilter] = useState<string>("ALL");

  // Selected file for download preparation
  const [selectedFile, setSelectedFile] = useState<HFGGUFFileItem | null>(null);
  const [preparedDownload, setPreparedDownload] = useState<PreparedDownloadPreview | null>(null);
  const [showPreparedModal, setShowPreparedModal] = useState(false);
  const [isEnqueueing, setIsEnqueueing] = useState(false);

  // Initial search on mount
  const executeSearch = useCallback(
    async (q: string, sort: string) => {
      const clean = q.trim();
      if (!clean) return;
      setIsSearching(true);
      setSearchError(null);
      setSelectedRepoId(null);
      setRepoDetails(null);
      setSelectedFile(null);

      try {
        const data = await searchHFModels(clean, 25, sort);
        setSearchResults(data.results);
      } catch (err: unknown) {
        setSearchError(
          err instanceof Error ? err.message : "Failed to search Hugging Face"
        );
      } finally {
        setIsSearching(false);
      }
    },
    []
  );

  useEffect(() => {
    executeSearch("qwen2.5", "downloads");
  }, [executeSearch]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setQuery(searchInput);
    executeSearch(searchInput, sortBy);
  };

  const handleSortChange = (newSort: string) => {
    setSortBy(newSort);
    executeSearch(searchInput, newSort);
  };

  const handleOpenRepo = async (repoId: string) => {
    setSelectedRepoId(repoId);
    setIsLoadingRepo(true);
    setRepoError(null);
    setSelectedFile(null);
    setSelectedQuantFilter("ALL");

    try {
      const details = await fetchHFRepositoryDetails(repoId);
      setRepoDetails(details);
      // Auto-select Q4_K_M or first GGUF file if available
      if (details.available_gguf_files.length > 0) {
        const q4km = details.available_gguf_files.find(
          (f) => f.quantization === "Q4_K_M"
        );
        setSelectedFile(q4km || details.available_gguf_files[0]);
      }
    } catch (err: unknown) {
      setRepoError(
        err instanceof Error ? err.message : "Failed to load repository details"
      );
    } finally {
      setIsLoadingRepo(false);
    }
  };

  const handlePrepareDownload = () => {
    if (!selectedRepoId || !selectedFile) return;

    // Build canonical destination and preview
    const cleanRepoFolder = selectedRepoId.replace("/", "--");
    const preview: PreparedDownloadPreview = {
      repo_id: selectedRepoId,
      filename: selectedFile.filename,
      size_gb: selectedFile.size_gb,
      size_bytes: selectedFile.size_bytes,
      quantization: selectedFile.quantization,
      download_url: selectedFile.download_url,
      destination_path: `models/${cleanRepoFolder}/${selectedFile.filename}`,
      sha256: selectedFile.sha256,
    };

    setPreparedDownload(preview);
    setShowPreparedModal(true);

    if (onFilePrepared) {
      onFilePrepared(preview);
    }
  };

  const handleStartDownload = async () => {
    if (!selectedRepoId || !selectedFile) return;
    setIsEnqueueing(true);
    setRepoError(null);
    try {
      const task = await createDownload({
        repo_id: selectedRepoId,
        filename: selectedFile.filename,
        revision: repoDetails?.revision || "main",
        expected_sha256: selectedFile.sha256 || undefined,
        total_bytes: selectedFile.size_bytes || undefined,
      });
      setShowPreparedModal(false);
      if (onDownloadStarted) {
        onDownloadStarted(task);
      }
    } catch (err: unknown) {
      setRepoError(err instanceof Error ? err.message : "Failed to start download");
    } finally {
      setIsEnqueueing(false);
    }
  };

  // Extract distinct quantizations for filtering
  const distinctQuants = repoDetails
    ? Array.from(new Set(repoDetails.available_gguf_files.map((f) => f.quantization))).sort()
    : [];

  const filteredGgufFiles = repoDetails
    ? selectedQuantFilter === "ALL"
      ? repoDetails.available_gguf_files
      : repoDetails.available_gguf_files.filter(
          (f) => f.quantization === selectedQuantFilter
        )
    : [];

  const formatDownloads = (num: number) => {
    if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`;
    if (num >= 1_000) return `${(num / 1_000).toFixed(1)}K`;
    return num.toLocaleString();
  };

  return (
    <div className="flex flex-col h-full overflow-hidden bg-[#07090E] text-slate-200">
      {/* Search Header Bar */}
      <div className="p-4 border-b border-white/[0.08] bg-[#0A0D17]/80 flex flex-col sm:flex-row gap-3 items-center justify-between">
        <form onSubmit={handleSearchSubmit} className="flex-1 flex gap-2 w-full">
          <div className="relative flex-1">
            <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-500" />
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search Hugging Face models (e.g. qwen2.5, llama-3.1, deepseek)..."
              className="w-full bg-[#0D1220] border border-white/10 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500/50 transition"
            />
          </div>
          <button
            type="submit"
            disabled={isSearching}
            className="px-4 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-600/20 transition disabled:opacity-50 flex items-center gap-1.5"
          >
            {isSearching ? (
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Search className="w-3.5 h-3.5" />
            )}
            <span>Search</span>
          </button>
        </form>

        <div className="flex items-center gap-2 self-end sm:self-auto text-xs text-slate-400">
          <span>Sort:</span>
          <select
            value={sortBy}
            onChange={(e) => handleSortChange(e.target.value)}
            className="bg-[#0D1220] border border-white/10 rounded-lg px-2.5 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
          >
            <option value="downloads">Most Downloads</option>
            <option value="likes">Most Likes</option>
            <option value="lastModified">Recently Updated</option>
          </select>
        </div>
      </div>

      {/* Error Banner */}
      {(searchError || repoError) && (
        <div className="px-6 py-2.5 bg-rose-950/40 border-b border-rose-500/30 flex items-center gap-2 text-xs text-rose-300">
          <AlertTriangle className="w-4 h-4 shrink-0 text-rose-400" />
          <span className="flex-1">{searchError || repoError}</span>
          <button
            onClick={() => {
              setSearchError(null);
              setRepoError(null);
            }}
            className="text-rose-400 hover:text-rose-200 text-xs font-semibold"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Main Content Area: Split View or Detail View */}
      <div className="flex-1 overflow-y-auto p-4 sm:p-6">
        {selectedRepoId ? (
          /* ================= REPOSITORY DETAILS VIEW ================= */
          <div className="space-y-4 max-w-4xl mx-auto">
            {/* Top Navigation */}
            <div className="flex items-center justify-between">
              <button
                onClick={() => setSelectedRepoId(null)}
                className="flex items-center gap-1.5 text-xs text-indigo-400 hover:text-indigo-300 transition"
              >
                <ChevronLeft className="w-4 h-4" />
                <span>Back to Search Results</span>
              </button>

              {repoDetails && (
                <a
                  href={repoDetails.repo_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200 transition"
                  title="View on Hugging Face"
                >
                  <span>Hugging Face Hub</span>
                  <ExternalLink className="w-3.5 h-3.5" />
                </a>
              )}
            </div>

            {isLoadingRepo ? (
              <div className="py-20 flex flex-col items-center justify-center gap-2 text-slate-500 text-xs">
                <RefreshCw className="w-6 h-6 animate-spin text-indigo-400" />
                <span>Loading repository metadata & GGUF files...</span>
              </div>
            ) : repoDetails ? (
              <div className="space-y-4">
                {/* Repository Header Card */}
                <div className="bg-[#0A0D17]/80 border border-white/[0.08] rounded-xl p-5 space-y-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 font-mono">
                          {repoDetails.author}
                        </span>
                        <h2 className="text-base font-semibold text-slate-100">
                          {repoDetails.repo_name}
                        </h2>
                      </div>
                      <p className="text-xs text-slate-400 mt-1 max-w-2xl">
                        {repoDetails.description || "No description provided."}
                      </p>
                    </div>

                    <div className="flex items-center gap-3 shrink-0 text-xs">
                      <span className="flex items-center gap-1 text-slate-400">
                        <Download className="w-3.5 h-3.5 text-indigo-400" />
                        {formatDownloads(repoDetails.downloads)}
                      </span>
                      <span className="flex items-center gap-1 text-slate-400">
                        <Heart className="w-3.5 h-3.5 text-rose-400" />
                        {repoDetails.likes}
                      </span>
                    </div>
                  </div>

                  {/* Badges Bar */}
                  <div className="flex items-center gap-2 flex-wrap text-[11px] pt-1 border-t border-white/[0.04]">
                    <span className="px-2 py-0.5 rounded bg-white/5 border border-white/10 text-slate-300">
                      License: {repoDetails.license || "Unknown"}
                    </span>
                    <span className="px-2 py-0.5 rounded bg-white/5 border border-white/10 text-slate-300">
                      Revision: {repoDetails.revision}
                    </span>
                    <span className="px-2 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 font-mono">
                      {repoDetails.gguf_files_count} GGUF {repoDetails.gguf_files_count === 1 ? "variant" : "variants"}
                    </span>
                    {repoDetails.model_card && (
                      <button
                        onClick={() => setShowModelCard(!showModelCard)}
                        className="flex items-center gap-1 text-indigo-400 hover:text-indigo-300 text-[11px] ml-auto transition"
                      >
                        <FileText className="w-3 h-3" />
                        <span>{showModelCard ? "Hide Model Card" : "View Model Card"}</span>
                      </button>
                    )}
                  </div>

                  {/* Model Card Drawer */}
                  {showModelCard && repoDetails.model_card && (
                    <div className="mt-3 p-4 bg-black/40 border border-white/[0.06] rounded-xl max-h-60 overflow-y-auto text-xs text-slate-300 whitespace-pre-wrap font-mono leading-relaxed">
                      {repoDetails.model_card}
                    </div>
                  )}
                </div>

                {/* GGUF Files Section */}
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
                      <Layers className="w-4 h-4 text-indigo-400" />
                      Available Quantization Weights
                    </h3>

                    {/* Quant Filter Pills */}
                    {distinctQuants.length > 1 && (
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <Filter className="w-3 h-3 text-slate-500 mr-1" />
                        <button
                          onClick={() => setSelectedQuantFilter("ALL")}
                          className={`px-2 py-0.5 rounded text-[10px] font-mono transition ${
                            selectedQuantFilter === "ALL"
                              ? "bg-indigo-600 text-white"
                              : "bg-white/5 text-slate-400 hover:text-slate-200"
                          }`}
                        >
                          All ({repoDetails.available_gguf_files.length})
                        </button>
                        {distinctQuants.map((q) => (
                          <button
                            key={q}
                            onClick={() => setSelectedQuantFilter(q)}
                            className={`px-2 py-0.5 rounded text-[10px] font-mono transition ${
                              selectedQuantFilter === q
                                ? "bg-indigo-600 text-white"
                                : "bg-white/5 text-slate-400 hover:text-slate-200"
                            }`}
                          >
                            {q}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* File List */}
                  {filteredGgufFiles.length === 0 ? (
                    <div className="p-8 text-center border border-dashed border-white/10 rounded-xl text-xs text-slate-500">
                      No GGUF files found for the selected filter.
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {filteredGgufFiles.map((file) => {
                        const isSelected = selectedFile?.filename === file.filename;
                        return (
                          <div
                            key={file.filename}
                            onClick={() => setSelectedFile(file)}
                            className={`p-3.5 rounded-xl border cursor-pointer transition flex items-center justify-between gap-3 ${
                              isSelected
                                ? "bg-indigo-950/20 border-indigo-500/50 shadow-[0_0_15px_rgba(99,102,241,0.08)]"
                                : "bg-[#0A0D17]/60 border-white/[0.06] hover:border-white/[0.12]"
                            }`}
                          >
                            <div className="flex items-center gap-3 min-w-0">
                              <div
                                className={`w-4 h-4 rounded-full border flex items-center justify-center shrink-0 ${
                                  isSelected
                                    ? "border-indigo-400 bg-indigo-600"
                                    : "border-white/20 bg-transparent"
                                }`}
                              >
                                {isSelected && (
                                  <div className="w-1.5 h-1.5 rounded-full bg-white" />
                                )}
                              </div>

                              <div className="space-y-0.5 min-w-0">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <span className="text-xs font-medium text-slate-100 truncate">
                                    {file.filename}
                                  </span>
                                  <span className="text-[10px] font-mono font-semibold px-2 py-0.5 rounded bg-indigo-500/15 text-indigo-300 border border-indigo-500/25">
                                    {file.quantization}
                                  </span>
                                </div>
                                {file.sha256 && (
                                  <div className="text-[10px] font-mono text-slate-500 truncate" title={file.sha256}>
                                    SHA: {file.sha256.slice(0, 16)}...
                                  </div>
                                )}
                              </div>
                            </div>

                            <div className="text-right shrink-0">
                              <span className="text-xs font-mono font-medium text-slate-200 block">
                                {file.size_gb ? `${file.size_gb} GB` : "Size unknown"}
                              </span>
                              {file.size_bytes && (
                                <span className="text-[10px] font-mono text-slate-500 block">
                                  {(file.size_bytes / 1024 / 1024).toFixed(0)} MB
                                </span>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>

                {/* Sticky Selection & Prepare Download Bar */}
                {selectedFile && (
                  <div className="sticky bottom-0 bg-[#0A0D17] border border-indigo-500/30 rounded-xl p-4 shadow-2xl flex flex-col sm:flex-row items-center justify-between gap-3 backdrop-blur-md">
                    <div className="space-y-0.5 w-full sm:w-auto">
                      <div className="text-[11px] text-slate-400">Selected Quantization File:</div>
                      <div className="text-xs font-medium text-slate-100 flex items-center gap-2">
                        <span className="truncate max-w-sm">{selectedFile.filename}</span>
                        <span className="text-indigo-400 font-mono font-semibold">
                          ({selectedFile.size_gb ? `${selectedFile.size_gb} GB` : "Unknown size"})
                        </span>
                      </div>
                    </div>

                    <button
                      onClick={handlePrepareDownload}
                      className="w-full sm:w-auto flex items-center justify-center gap-2 px-5 py-2.5 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-600/30 transition shrink-0"
                    >
                      <Download className="w-4 h-4" />
                      <span>Prepare Download</span>
                    </button>
                  </div>
                )}
              </div>
            ) : null}
          </div>
        ) : (
          /* ================= SEARCH RESULTS GRID ================= */
          <div className="space-y-4 max-w-5xl mx-auto">
            <div className="flex items-center justify-between text-xs text-slate-400">
              <span>
                Found <strong>{searchResults.length}</strong> GGUF repositories for "
                <span className="text-slate-200">{query}</span>"
              </span>
            </div>

            {isSearching ? (
              <div className="py-20 flex flex-col items-center justify-center gap-2 text-slate-500 text-xs">
                <RefreshCw className="w-6 h-6 animate-spin text-indigo-400" />
                <span>Searching Hugging Face Hub...</span>
              </div>
            ) : searchResults.length === 0 ? (
              <div className="py-20 text-center border border-dashed border-white/10 rounded-2xl bg-white/[0.01] p-8">
                <Search className="w-10 h-10 mx-auto text-slate-600 mb-3" />
                <h3 className="text-sm font-medium text-slate-300">No GGUF models matched your query</h3>
                <p className="text-xs text-slate-500 mt-1 max-w-sm mx-auto">
                  Try searching for popular open-weight models like <code className="text-indigo-300">qwen2.5</code>, <code className="text-indigo-300">llama-3.1</code>, or <code className="text-indigo-300">phi-3</code>.
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {searchResults.map((repo) => (
                  <div
                    key={repo.repo_id}
                    onClick={() => handleOpenRepo(repo.repo_id)}
                    className="p-4 rounded-xl border border-white/[0.07] bg-[#0A0D17]/60 hover:border-indigo-500/40 hover:bg-[#0D1222]/80 transition cursor-pointer flex flex-col justify-between gap-3 group"
                  >
                    <div className="space-y-2">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <span className="text-[10px] font-mono uppercase px-1.5 py-0.5 rounded bg-white/5 text-slate-400 border border-white/5">
                            {repo.author}
                          </span>
                          <h4 className="text-sm font-semibold text-slate-100 group-hover:text-indigo-300 transition truncate mt-1">
                            {repo.repo_name}
                          </h4>
                        </div>
                        {repo.license && (
                          <span className="text-[10px] px-2 py-0.5 rounded bg-white/5 text-slate-400 border border-white/10 shrink-0">
                            {repo.license}
                          </span>
                        )}
                      </div>

                      <p className="text-xs text-slate-400 line-clamp-2">
                        {repo.description || "No description provided."}
                      </p>
                    </div>

                    <div className="flex items-center justify-between text-xs text-slate-500 pt-2 border-t border-white/[0.04]">
                      <div className="flex items-center gap-3">
                        <span className="flex items-center gap-1">
                          <Download className="w-3.5 h-3.5 text-indigo-400" />
                          {formatDownloads(repo.downloads)}
                        </span>
                        <span className="flex items-center gap-1">
                          <Heart className="w-3.5 h-3.5 text-rose-400" />
                          {repo.likes}
                        </span>
                      </div>

                      <span className="text-[11px] text-indigo-400 group-hover:translate-x-0.5 transition font-medium">
                        View GGUF Files →
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Prepare Download Preview Modal (Stage 3 Verification Modal) */}
      {showPreparedModal && preparedDownload && (
        <div className="fixed inset-0 z-60 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-lg bg-[#0D1220] border border-indigo-500/30 rounded-2xl p-6 shadow-2xl space-y-4">
            <div className="flex items-center gap-3">
              <div className="p-3 rounded-xl bg-indigo-500/15 border border-indigo-500/30 text-indigo-400">
                <FileCheck className="w-6 h-6" />
              </div>
              <div>
                <h3 className="text-base font-semibold text-slate-100">
                  Download Request Prepared
                </h3>
                <p className="text-xs text-slate-400">
                  Stage 3 file selection and validation complete.
                </p>
              </div>
            </div>

            <div className="bg-black/30 border border-white/5 rounded-xl p-3.5 text-xs space-y-2 font-mono text-slate-300">
              <div>
                <span className="text-slate-500">Repository:</span>{" "}
                <span className="text-indigo-300 font-semibold">{preparedDownload.repo_id}</span>
              </div>
              <div>
                <span className="text-slate-500">Filename:</span>{" "}
                <span className="text-slate-200">{preparedDownload.filename}</span>
              </div>
              <div className="flex justify-between">
                <div>
                  <span className="text-slate-500">Quantization:</span>{" "}
                  <span className="text-emerald-400">{preparedDownload.quantization}</span>
                </div>
                <div>
                  <span className="text-slate-500">Size:</span>{" "}
                  <span>{preparedDownload.size_gb} GB</span>
                </div>
              </div>
              <div>
                <span className="text-slate-500">Destination:</span>{" "}
                <span className="text-slate-400">{preparedDownload.destination_path}</span>
              </div>
              <div className="truncate">
                <span className="text-slate-500">Source:</span>{" "}
                <span className="text-slate-500 truncate" title={preparedDownload.download_url}>
                  {preparedDownload.download_url}
                </span>
              </div>
            </div>

            <div className="p-3 bg-indigo-950/20 border border-indigo-500/20 rounded-xl text-xs text-indigo-200 flex items-start gap-2">
              <ShieldCheck className="w-4 h-4 text-indigo-400 shrink-0 mt-0.5" />
              <span>
                <strong>Asynchronous Download Ready:</strong> The model will be downloaded in the background with HTTP Range resume, checksum verification, and automatic registration into your Local Model Library.
              </span>
            </div>

            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                onClick={() => setShowPreparedModal(false)}
                className="px-4 py-2 rounded-xl text-xs font-medium bg-white/5 hover:bg-white/10 text-slate-300 border border-white/10 transition"
              >
                Cancel
              </button>
              <button
                onClick={handleStartDownload}
                disabled={isEnqueueing}
                className="flex items-center gap-1.5 px-5 py-2 rounded-xl text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-600/30 transition disabled:opacity-50"
              >
                {isEnqueueing ? (
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Download className="w-3.5 h-3.5" />
                )}
                <span>{isEnqueueing ? "Enqueuing..." : "Start Download"}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
