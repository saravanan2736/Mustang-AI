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
    <div className="flex flex-col h-full overflow-hidden bg-transparent text-fg">
      {/* Search Header Bar */}
      <div className="p-4 border-b border-line/50 bg-panel/30 flex flex-col sm:flex-row gap-3 items-center justify-between">
        <form onSubmit={handleSearchSubmit} className="flex-1 flex gap-2 w-full">
          <div className="relative flex-1">
            <Search className="w-4 h-4 absolute left-3 top-2.5 text-subtle" />
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search Hugging Face models (e.g. qwen2.5, llama-3.1, deepseek)..."
              className="w-full bg-panel/80 border border-line/70 rounded-xl pl-9 pr-3 py-1.5 text-xs text-fg placeholder:text-subtle focus:outline-none focus:border-accent/80 focus:ring-1 focus:ring-accent/30 transition-all"
            />
          </div>
          <button
            type="submit"
            disabled={isSearching}
            className="px-4 py-1.5 rounded-xl text-xs font-semibold btn-violet text-white hover:brightness-110 transition-all disabled:opacity-50 flex items-center gap-1.5 shrink-0"
          >
            {isSearching ? (
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Search className="w-3.5 h-3.5" />
            )}
            <span>Search</span>
          </button>
        </form>

        <div className="flex items-center gap-2 self-end sm:self-auto text-xs text-muted">
          <span>Sort:</span>
          <select
            value={sortBy}
            onChange={(e) => handleSortChange(e.target.value)}
            className="bg-panel/80 border border-line/70 rounded-xl px-2.5 py-1 text-xs text-fg focus:outline-none focus:border-accent/80 transition-all"
          >
            <option value="downloads">Most Downloads</option>
            <option value="likes">Most Likes</option>
            <option value="lastModified">Recently Updated</option>
          </select>
        </div>
      </div>

      {/* Error Banner */}
      {(searchError || repoError) && (
        <div className="px-6 py-2.5 bg-danger/10 border-b border-danger/40 flex items-center gap-2 text-xs text-danger">
          <AlertTriangle className="w-4 h-4 shrink-0 text-danger" />
          <span className="flex-1">{searchError || repoError}</span>
          <button
            onClick={() => {
              setSearchError(null);
              setRepoError(null);
            }}
            className="text-danger hover:opacity-80 text-xs font-semibold"
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
                className="flex items-center gap-1.5 text-xs text-link hover:underline transition"
              >
                <ChevronLeft className="w-4 h-4" />
                <span>Back to Search Results</span>
              </button>

              {repoDetails && (
                <a
                  href={repoDetails.repo_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1 text-xs text-muted hover:text-fg transition"
                  title="View on Hugging Face"
                >
                  <span>Hugging Face Hub</span>
                  <ExternalLink className="w-3.5 h-3.5" />
                </a>
              )}
            </div>

            {isLoadingRepo ? (
              <div className="py-20 flex flex-col items-center justify-center gap-2 text-subtle text-xs">
                <RefreshCw className="w-6 h-6 animate-spin text-link" />
                <span>Loading repository metadata & GGUF files...</span>
              </div>
            ) : repoDetails ? (
              <div className="space-y-4">
                {/* Repository Header Card */}
                <div className="bg-canvas border border-line rounded-md p-5 space-y-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs px-2 py-0.5 rounded bg-accent/10 text-link border border-accent font-mono">
                          {repoDetails.author}
                        </span>
                        <h2 className="text-base font-semibold text-fg">
                          {repoDetails.repo_name}
                        </h2>
                      </div>
                      <p className="text-xs text-muted mt-1 max-w-2xl">
                        {repoDetails.description || "No description provided."}
                      </p>
                    </div>

                    <div className="flex items-center gap-3 shrink-0 text-xs">
                      <span className="flex items-center gap-1 text-muted">
                        <Download className="w-3.5 h-3.5 text-link" />
                        {formatDownloads(repoDetails.downloads)}
                      </span>
                      <span className="flex items-center gap-1 text-muted">
                        <Heart className="w-3.5 h-3.5 text-danger" />
                        {repoDetails.likes}
                      </span>
                    </div>
                  </div>

                  {/* Badges Bar */}
                  <div className="flex items-center gap-2 flex-wrap text-xs pt-1 border-t border-line">
                    <span className="px-2 py-0.5 rounded bg-raised border border-line text-fg">
                      License: {repoDetails.license || "Unknown"}
                    </span>
                    <span className="px-2 py-0.5 rounded bg-raised border border-line text-fg">
                      Revision: {repoDetails.revision}
                    </span>
                    <span className="px-2 py-0.5 rounded bg-success/10 border border-success/40 text-success font-mono">
                      {repoDetails.gguf_files_count} GGUF {repoDetails.gguf_files_count === 1 ? "variant" : "variants"}
                    </span>
                    {repoDetails.model_card && (
                      <button
                        onClick={() => setShowModelCard(!showModelCard)}
                        className="flex items-center gap-1 text-link hover:underline text-xs ml-auto transition"
                      >
                        <FileText className="w-3 h-3" />
                        <span>{showModelCard ? "Hide Model Card" : "View Model Card"}</span>
                      </button>
                    )}
                  </div>

                  {/* Model Card Drawer */}
                  {showModelCard && repoDetails.model_card && (
                    <div className="mt-3 p-4 bg-canvas border border-line rounded-md max-h-60 overflow-y-auto text-xs text-fg whitespace-pre-wrap font-mono leading-relaxed">
                      {repoDetails.model_card}
                    </div>
                  )}
                </div>

                {/* GGUF Files Section */}
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-fg flex items-center gap-2">
                      <Layers className="w-4 h-4 text-link" />
                      Available Quantization Weights
                    </h3>

                    {/* Quant Filter Pills */}
                    {distinctQuants.length > 1 && (
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <Filter className="w-3 h-3 text-subtle mr-1" />
                        <button
                          onClick={() => setSelectedQuantFilter("ALL")}
                          className={`px-2 py-0.5 rounded text-xs font-mono transition ${
                            selectedQuantFilter === "ALL"
                              ? "bg-accent text-fg"
                              : "bg-raised text-muted hover:text-fg"
                          }`}
                        >
                          All ({repoDetails.available_gguf_files.length})
                        </button>
                        {distinctQuants.map((q) => (
                          <button
                            key={q}
                            onClick={() => setSelectedQuantFilter(q)}
                            className={`px-2 py-0.5 rounded text-xs font-mono transition ${
                              selectedQuantFilter === q
                                ? "bg-accent text-fg"
                                : "bg-raised text-muted hover:text-fg"
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
                    <div className="p-8 text-center border border-dashed border-line rounded-md text-xs text-subtle">
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
                            className={`p-3.5 rounded-xl border cursor-pointer transition-all flex items-center justify-between gap-3 ${
                              isSelected
                                ? "bg-accent/15 border-accent/80"
                                : "bg-panel/50 border-line/60 hover:border-line hover:bg-panel/80"
                            }`}
                          >
                            <div className="flex items-center gap-3 min-w-0">
                              <div
                                className={`w-4 h-4 rounded-full border flex items-center justify-center shrink-0 transition-colors ${
                                  isSelected
                                    ? "border-accent bg-accent"
                                    : "border-line/70 bg-transparent"
                                }`}
                              >
                                {isSelected && (
                                  <div className="w-1.5 h-1.5 rounded-full bg-white" />
                                )}
                              </div>

                              <div className="space-y-0.5 min-w-0">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <span className="text-xs font-medium text-fg truncate">
                                    {file.filename}
                                  </span>
                                  <span className="text-[11px] font-mono font-semibold px-2 py-0.5 rounded-md bg-accent/20 text-link border border-accent/40">
                                    {file.quantization}
                                  </span>
                                </div>
                                {file.sha256 && (
                                  <div className="text-[11px] font-mono text-subtle truncate" title={file.sha256}>
                                    SHA: {file.sha256.slice(0, 16)}...
                                  </div>
                                )}
                              </div>
                            </div>

                            <div className="text-right shrink-0">
                              <span className="text-xs font-mono font-medium text-fg block">
                                {file.size_gb ? `${file.size_gb} GB` : "Size unknown"}
                              </span>
                              {file.size_bytes && (
                                <span className="text-[11px] font-mono text-subtle block">
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
                  <div className="sticky bottom-0 bg-panel/95 border border-accent/60 rounded-2xl p-4 shadow-2xl flex flex-col sm:flex-row items-center justify-between gap-3">
                    <div className="space-y-0.5 w-full sm:w-auto">
                      <div className="text-xs text-muted">Selected Quantization File:</div>
                      <div className="text-xs font-medium text-fg flex items-center gap-2">
                        <span className="truncate max-w-sm font-semibold">{selectedFile.filename}</span>
                        <span className="text-accent font-mono font-semibold">
                          ({selectedFile.size_gb ? `${selectedFile.size_gb} GB` : "Unknown size"})
                        </span>
                      </div>
                    </div>

                    <button
                      onClick={handlePrepareDownload}
                      className="w-full sm:w-auto flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl text-xs font-semibold btn-violet text-white hover:brightness-110 transition-all shrink-0"
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
            <div className="flex items-center justify-between text-xs text-muted">
              <span>
                Found <strong>{searchResults.length}</strong> GGUF repositories for "
                <span className="text-fg">{query}</span>"
              </span>
            </div>

            {isSearching ? (
              <div className="py-20 flex flex-col items-center justify-center gap-2 text-subtle text-xs">
                <RefreshCw className="w-6 h-6 animate-spin text-link" />
                <span>Searching Hugging Face Hub...</span>
              </div>
            ) : searchResults.length === 0 ? (
              <div className="py-20 text-center border border-dashed border-line rounded-lg bg-raised p-8">
                <Search className="w-10 h-10 mx-auto text-subtle mb-3" />
                <h3 className="text-sm font-medium text-fg">No GGUF models matched your query</h3>
                <p className="text-xs text-subtle mt-1 max-w-sm mx-auto">
                  Try searching for popular open-weight models like <code className="text-link">qwen2.5</code>, <code className="text-link">llama-3.1</code>, or <code className="text-link">phi-3</code>.
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
                {searchResults.map((repo) => (
                  <div
                    key={repo.repo_id}
                    onClick={() => handleOpenRepo(repo.repo_id)}
                    className="p-4 rounded-2xl border border-line/60 bg-panel/60 hover:border-accent/60 hover:bg-panel/85 transition-all cursor-pointer flex flex-col justify-between gap-3 group shadow-sm"
                  >
                    <div className="space-y-2">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <span className="text-[11px] font-mono px-2 py-0.5 rounded-lg bg-raised/80 text-accent border border-accent/30 font-medium">
                            {repo.author}
                          </span>
                          <h4 className="text-sm font-semibold text-fg group-hover:text-accent transition truncate mt-1.5 tracking-tight">
                            {repo.repo_name}
                          </h4>
                        </div>
                        {repo.license && (
                          <span className="text-[11px] px-2 py-0.5 rounded-lg bg-raised/80 text-muted border border-line/60 shrink-0">
                            {repo.license}
                          </span>
                        )}
                      </div>

                      <p className="text-xs text-muted line-clamp-2 leading-relaxed">
                        {repo.description || "No description provided."}
                      </p>
                    </div>

                    <div className="flex items-center justify-between text-xs text-subtle pt-2 border-t border-line/40">
                      <div className="flex items-center gap-3">
                        <span className="flex items-center gap-1 font-mono">
                          <Download className="w-3.5 h-3.5 text-accent" />
                          {formatDownloads(repo.downloads)}
                        </span>
                        <span className="flex items-center gap-1 font-mono">
                          <Heart className="w-3.5 h-3.5 text-danger" />
                          {repo.likes}
                        </span>
                      </div>

                      <span className="text-xs text-accent group-hover:text-accent-hover transition font-medium">
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
        <div className="fixed inset-0 z-60 flex items-center justify-center p-4 bg-black/70">
          <div className="w-full max-w-lg bg-panel/95 border border-accent/60 rounded-2xl p-6 shadow-2xl space-y-4">
            <div className="flex items-center gap-3">
              <div className="p-3 rounded-xl bg-accent/15 border border-accent/30 text-accent">
                <FileCheck className="w-6 h-6" />
              </div>
              <div>
                <h3 className="text-base font-semibold text-fg tracking-tight">
                  Download Request Prepared
                </h3>
                <p className="text-xs text-muted">
                  Stage 3 file selection and validation complete.
                </p>
              </div>
            </div>

            <div className="bg-canvas/90 border border-line/60 rounded-xl p-3.5 text-xs space-y-2 font-mono text-fg">
              <div>
                <span className="text-subtle">Repository:</span>{" "}
                <span className="text-accent font-semibold">{preparedDownload.repo_id}</span>
              </div>
              <div>
                <span className="text-subtle">Filename:</span>{" "}
                <span className="text-fg">{preparedDownload.filename}</span>
              </div>
              <div className="flex justify-between">
                <div>
                  <span className="text-subtle">Quantization:</span>{" "}
                  <span className="text-success font-semibold">{preparedDownload.quantization}</span>
                </div>
                <div>
                  <span className="text-subtle">Size:</span>{" "}
                  <span className="font-semibold">{preparedDownload.size_gb} GB</span>
                </div>
              </div>
              <div>
                <span className="text-subtle">Destination:</span>{" "}
                <span className="text-muted">{preparedDownload.destination_path}</span>
              </div>
              <div className="truncate">
                <span className="text-subtle">Source:</span>{" "}
                <span className="text-subtle truncate" title={preparedDownload.download_url}>
                  {preparedDownload.download_url}
                </span>
              </div>
            </div>

            <div className="p-3 bg-accent/10 border border-accent/30 rounded-xl text-xs text-link flex items-start gap-2.5">
              <ShieldCheck className="w-4 h-4 text-accent shrink-0 mt-0.5" />
              <span className="leading-relaxed">
                <strong className="text-fg font-semibold">Asynchronous Download Ready:</strong> The model will be downloaded in the background with HTTP Range resume, checksum verification, and automatic registration into your Local Model Library.
              </span>
            </div>

            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                onClick={() => setShowPreparedModal(false)}
                className="px-4 py-2 rounded-xl text-xs font-medium bg-raised/80 hover:bg-raised text-fg border border-line/70 transition-all"
              >
                Cancel
              </button>
              <button
                onClick={handleStartDownload}
                disabled={isEnqueueing}
                className="flex items-center gap-1.5 px-5 py-2 rounded-xl text-xs font-semibold btn-violet text-white hover:brightness-110 transition-all disabled:opacity-50"
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
