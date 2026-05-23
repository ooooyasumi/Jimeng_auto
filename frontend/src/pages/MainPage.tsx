import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import {
  fetchTasks, fetchQueueStatus, fetchCredit,
  deleteTask, reorderTask, pauseQueue, resumeQueue,
  createTask, getPresignedUpload, proxyUpload,
} from "../api";
import type { Task, QueueStatus, Reference } from "../api";

const REFRESH_INTERVAL = 15;

// Upload state tracking (not persisted, just for the current session)
interface UploadEntry { file: File; progress: number; }

const FILE_LIMITS: Record<string, { maxSize: number; label: string }> = {
  image: { maxSize: 10 * 1024 * 1024, label: "10 MB" },
  video: { maxSize: 200 * 1024 * 1024, label: "200 MB" },
  audio: { maxSize: 10 * 1024 * 1024, label: "10 MB" },
};
const MAX_IMAGE_COUNT = 9;
const MAX_VIDEO_COUNT = 3;
const MAX_AUDIO_COUNT = 3;
const MAX_PROMPT_LENGTH = 5000;

function detectType(filename: string): Reference["type"] {
  const ext = filename.split(".").pop()?.toLowerCase() || "";
  if (["mp4", "mov", "webm", "avi"].includes(ext)) return "video";
  if (["mp3", "wav", "aac", "m4a", "ogg"].includes(ext)) return "audio";
  return "image";
}

function validateFileCounts(type: Reference["type"], existingRefs: Reference[], uploadingCount: number): string | null {
  const counts = { image: 0, video: 0, audio: 0 };
  for (const r of existingRefs) counts[r.type]++;
  counts[type]++;
  if (counts.image + (type === "image" ? uploadingCount : 0) > MAX_IMAGE_COUNT) return `图片最多 ${MAX_IMAGE_COUNT} 张`;
  if (counts.video + (type === "video" ? uploadingCount : 0) > MAX_VIDEO_COUNT) return `视频最多 ${MAX_VIDEO_COUNT} 个`;
  if (counts.audio + (type === "audio" ? uploadingCount : 0) > MAX_AUDIO_COUNT) return `音频最多 ${MAX_AUDIO_COUNT} 个`;
  return null;
}

async function validateFile(file: File, existingRefs: Reference[], uploadingCount: number): Promise<string | null> {
  const type = detectType(file.name);
  const limit = FILE_LIMITS[type];
  if (file.size > limit.maxSize) {
    return `${file.name} 超过 ${limit.label} 限制 (${(file.size / 1024 / 1024).toFixed(1)} MB)`;
  }
  const countErr = validateFileCounts(type, existingRefs, uploadingCount);
  if (countErr) return countErr;

  // Check duration for video/audio
  if (type === "video" || type === "audio") {
    const minDur = type === "audio" ? 2 : 4;
    const maxDur = 15;
    try {
      const dur = await getMediaDuration(file, type);
      if (dur < minDur) return `${file.name} 时长 ${dur.toFixed(1)}s, 需 ≥ ${minDur}s`;
      if (dur > maxDur) return `${file.name} 时长 ${dur.toFixed(1)}s, 需 ≤ ${maxDur}s`;
    } catch {
      return `无法读取 ${file.name} 的时长，文件可能已损坏`;
    }
  }
  return null;
}

function getMediaDuration(file: File, type: "video" | "audio"): Promise<number> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const el = document.createElement(type);
    el.preload = "metadata";
    el.onloadedmetadata = () => { URL.revokeObjectURL(url); resolve(el.duration); };
    el.onerror = () => { URL.revokeObjectURL(url); reject(new Error("load failed")); };
    el.src = url;
  });
}

// Build the raw COS URL (standard domain) from custom domain URL or key
function rawCosUrl(cosUrlOrKey: string): string {
  if (cosUrlOrKey.includes("myqcloud.com")) return cosUrlOrKey;
  if (cosUrlOrKey.startsWith("https://")) {
    const path = cosUrlOrKey.replace(/^https:\/\/[^/]+\//, "");
    return `https://jimengauto-1372876299.cos.ap-chongqing.myqcloud.com/${path}`;
  }
  return `https://jimengauto-1372876299.cos.ap-chongqing.myqcloud.com/${cosUrlOrKey}`;
}

function calcTaskCost(duration: number, modelVersion: string, refs: { type: string }[]): number {
  const hasVideo = refs.some(r => r.type === "video");
  if (modelVersion === "seedance2.0") return hasVideo ? duration * 16 : duration * 8;
  return hasVideo ? duration * 10 : duration * 5;
}

export default function MainPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [queueStatus, setQueueStatus] = useState<QueueStatus | null>(null);
  const [credit, setCredit] = useState<number | null>(null);
  const [refs, setRefs] = useState<Reference[]>([]);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [dragId, setDragId] = useState<number | null>(null);

  // Upload progress: filename -> {file, progress}
  const [uploading, setUploading] = useState<Map<string, UploadEntry>>(new Map());

  const [prompt, setPrompt] = useState("");
  const [duration, setDuration] = useState(5);
  const [ratio, setRatio] = useState("16:9");
  const [modelVersion, setModelVersion] = useState("seedance2.0fast");
  const [submitting, setSubmitting] = useState(false);

  const [showMention, setShowMention] = useState(false);
  const [mentionFilter, setMentionFilter] = useState("");
  const [previewRef, setPreviewRef] = useState<Reference | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [refreshState, setRefreshState] = useState<"idle" | "loading" | "done">("idle");

  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const bottomBarRef = useRef<HTMLDivElement>(null);

  const hasRefs = refs.length > 0;
  const isUploading = uploading.size > 0;
  const modeLabel = hasRefs || isUploading ? "全能参考 (multimodal2video)" : "文生视频 (text2video)";

  // Estimated credit cost
  const estimatedCost = (() => {
    const hasVideoRef = refs.some(r => r.type === "video");
    if (modelVersion === "seedance2.0") {
      return hasVideoRef ? duration * 16 : duration * 8;
    }
    // seedance2.0fast (default)
    return hasVideoRef ? duration * 10 : duration * 5;
  })();

  function showToast(msg: string) { setToast(msg); setTimeout(() => setToast(null), 3000); }

  const refresh = useCallback(async () => {
    setRefreshState("loading");
    try {
      const [td, qs, cd] = await Promise.all([fetchTasks(), fetchQueueStatus(), fetchCredit()]);
      setTasks(td); setQueueStatus(qs);
      if (cd?.total_credit) setCredit(cd.total_credit);
      setLastRefresh(new Date());
    } catch (err) { console.error("Refresh failed", err); }
    setTimeout(() => setRefreshState("idle"), 400);
  }, []);

  useEffect(() => { refresh(); const t = setInterval(refresh, REFRESH_INTERVAL * 1000); return () => clearInterval(t); }, [refresh]);

  // Full-page drag-and-drop
  useEffect(() => {
    let c = 0;
    const enter = (e: DragEvent) => { e.preventDefault(); c++; if (e.dataTransfer?.types.includes("Files")) document.body.classList.add("body--drag-over"); };
    const leave = () => { c--; if (c <= 0) { c = 0; document.body.classList.remove("body--drag-over"); } };
    const over = (e: DragEvent) => { e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = "copy"; };
    const drop = (e: DragEvent) => { e.preventDefault(); c = 0; document.body.classList.remove("body--drag-over"); if (e.dataTransfer?.files?.length) startUploads(e.dataTransfer.files); };
    document.addEventListener("dragenter", enter); document.addEventListener("dragleave", leave);
    document.addEventListener("dragover", over); document.addEventListener("drop", drop);
    return () => { document.removeEventListener("dragenter", enter); document.removeEventListener("dragleave", leave); document.removeEventListener("dragover", over); document.removeEventListener("drop", drop); };
  }, [refs, uploading]);

  // ===== Upload with XHR progress =====
  async function startUploads(files: FileList | File[]) {
    const arr = Array.from(files);
    for (const file of arr) {
      const err = await validateFile(file, refs, uploading.size);
      if (err) { showToast(err); continue; }
      // Mark as uploading
      setUploading(prev => {
        const next = new Map(prev);
        next.set(file.name, { file, progress: 0 });
        return next;
      });
      doUpload(file);
    }
  }

  async function doUpload(file: File) {
    try {
      let cos_url: string;
      // Try presigned direct upload with XHR for progress
      try {
        const { upload_url, cos_url: presignedCosUrl } = await getPresignedUpload(file.name);
        await xhrUpload(upload_url, file, (pct) => {
          setUploading(prev => { const n = new Map(prev); const e = n.get(file.name); if (e) n.set(file.name, { ...e, progress: pct }); return n; });
        });
        cos_url = presignedCosUrl;
      } catch {
        // Fallback proxy — no progress for proxy, just jump to 100
        const result = await proxyUpload(file);
        cos_url = result.cos_url;
      }
      const type = detectType(file.name);
      setRefs(prev => [...prev, { type, cos_url, filename: file.name }]);
    } catch (err: any) {
      showToast(`${file.name} 上传失败: ${err.message}`);
    } finally {
      setUploading(prev => { const n = new Map(prev); n.delete(file.name); return n; });
    }
  }

  function handleFileInput(e: React.ChangeEvent<HTMLInputElement>) {
    if (e.target.files?.length) startUploads(e.target.files);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handleFileDragOver(e: React.DragEvent) { e.preventDefault(); e.stopPropagation(); e.dataTransfer.dropEffect = "copy"; }
  function handleFileDrop(e: React.DragEvent) { e.preventDefault(); e.stopPropagation(); document.body.classList.remove("body--drag-over"); if (e.dataTransfer.files?.length) startUploads(e.dataTransfer.files); }

  function handlePaste(e: React.ClipboardEvent) {
    const items = e.clipboardData?.items; if (!items) return;
    const files: File[] = [];
    for (const item of Array.from(items)) { if (item.kind === "file") { const f = item.getAsFile(); if (f) files.push(f); } }
    if (files.length > 0) { e.preventDefault(); startUploads(files); }
  }

  function removeRef(index: number) {
    setRefs(prev => prev.filter((_, i) => i !== index));
  }

  // ===== @ mention =====
  function handlePromptChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    const value = e.target.value; setPrompt(value);
    const ta = e.target; ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
    const cursorPos = e.target.selectionStart || 0;
    const textBefore = value.slice(0, cursorPos);
    const atMatch = textBefore.match(/@([^\s@]*)$/);
    if (atMatch && refs.length > 0) { setMentionFilter(atMatch[1].toLowerCase()); setShowMention(true); }
    else { setShowMention(false); }
  }

  function insertMention(ref: Reference) {
    const cursorPos = textareaRef.current?.selectionStart || prompt.length;
    const textBefore = prompt.slice(0, cursorPos);
    const textAfter = prompt.slice(cursorPos);
    const atIdx = textBefore.lastIndexOf("@");
    setPrompt(textBefore.slice(0, atIdx) + ref.filename + " " + textAfter);
    setShowMention(false);
    textareaRef.current?.focus();
  }

  const filteredMentions = useMemo(() => {
    const ready = refs;
    if (!mentionFilter) return ready;
    return ready.filter(r => r.filename.toLowerCase().includes(mentionFilter));
  }, [refs, mentionFilter]);

  // ===== Submit =====
  async function handleSubmit() {
    if (!prompt.trim() || isUploading) return;
    if (prompt.length > MAX_PROMPT_LENGTH) { showToast(`提示词最多 ${MAX_PROMPT_LENGTH} 字`); return; }
    setSubmitting(true);
    try {
      await createTask({ prompt: prompt.trim(), duration, ratio, model_version: modelVersion, references: refs });
      setPrompt(""); setRefs([]); setShowMention(false); refresh();
    } catch (err: any) { showToast(err.message || "提交失败"); }
    finally { setSubmitting(false); }
  }

  async function handleDelete(id: number) { try { await deleteTask(id); refresh(); } catch (e: any) { showToast(e.message); } }
  async function handleReorder(id: number, pos: number) { try { await reorderTask(id, pos); refresh(); } catch (e: any) { showToast(e.message); } }
  async function handlePauseResume() { try { queueStatus?.paused ? await resumeQueue() : await pauseQueue(); refresh(); } catch (e: any) { showToast(e.message); } }

  function handleDragStart(e: React.DragEvent, taskId: number) { setDragId(taskId); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(taskId)); (e.currentTarget as HTMLElement).classList.add("task-card--dragging"); }
  function handleDragEnd(e: React.DragEvent) { setDragId(null); (e.currentTarget as HTMLElement).classList.remove("task-card--dragging"); }
  function handleDragOver(e: React.DragEvent, taskId: number) { e.preventDefault(); e.dataTransfer.dropEffect = "move"; if (dragId !== null && dragId !== taskId) (e.currentTarget as HTMLElement).classList.add("task-card--drag-over"); }
  function handleDragLeave(e: React.DragEvent) { (e.currentTarget as HTMLElement).classList.remove("task-card--drag-over"); }
  function handleDrop(e: React.DragEvent, targetTask: Task) { e.preventDefault(); (e.currentTarget as HTMLElement).classList.remove("task-card--drag-over"); const dId = Number(e.dataTransfer.getData("text/plain")); if (dId && dId !== targetTask.id) handleReorder(dId, targetTask.position); setDragId(null); }

  const sortedDone = tasks.filter(t => t.status === "done" || t.status === "failed").sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
  const pendingTasks = tasks.filter(t => t.status === "pending").sort((a, b) => a.position - b.position);

  function formatTime(ts: string) { const d = new Date(ts + "Z"); const diff = Date.now() - d.getTime(); const m = Math.floor(diff / 60000); if (m < 1) return "刚刚"; if (m < 60) return `${m} 分钟前`; const h = Math.floor(m / 60); if (h < 24) return `${h} 小时前`; return `${Math.floor(h / 24)} 天前`; }
  function refEmoji(type: string) { switch (type) { case "image": return "🖼"; case "video": return "🎬"; case "audio": return "🎵"; default: return "?"; } }

  // Upload progress percentage across all uploading files
  const overallProgress = uploading.size > 0
    ? Math.round(Array.from(uploading.values()).reduce((s, e) => s + e.progress, 0) / uploading.size)
    : 100;

  return (
    <>
      {previewRef && <FilePreview refData={previewRef} onClose={() => setPreviewRef(null)} />}
      {toast && <div className="toast">{toast}</div>}

      <div className="topbar">
        <div className="topbar__inner container">
          <div className="topbar__title">即梦视频队列</div>
          <div className="topbar__stats">
            <div className="topbar__stat">生成中<span className="topbar__stat-num">{queueStatus?.running ? 1 : 0}</span></div>
            <div className="topbar__stat">排队中<span className="topbar__stat-num">{queueStatus?.pending_count || 0}</span></div>
            <div className="topbar__stat">已完成<span className="topbar__stat-num">{queueStatus?.done_count || 0}</span></div>
          </div>
          <div className="topbar__right">
            {lastRefresh && (
              <span className="topbar__refresh">
                更新时间:<span className="topbar__refresh-time">{lastRefresh.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
                <span className="topbar__refresh-tooltip">数据每 {REFRESH_INTERVAL} 秒自动刷新</span>
              </span>
            )}
            <button className={`topbar__btn ${refreshState === "loading" ? "topbar__btn--pressed" : ""}`} onClick={() => refresh()} disabled={refreshState === "loading"} title="立即刷新">↻ 刷新</button>
            {credit !== null && <span className="topbar__credit">✦ {credit.toLocaleString()} 积分</span>}
            <button className="topbar__btn" onClick={() => { localStorage.removeItem("token"); window.location.reload(); }}>退出</button>
          </div>
        </div>
      </div>

      <div className="main">
        <div className="main__inner container">
          <div className="section-header">生成中 / 已完成<span className="section-header__line"></span></div>
          {queueStatus?.running && <TaskCard task={queueStatus.running} isActive formatTime={formatTime} />}
          {sortedDone.map(t => <TaskCard key={t.id} task={t} formatTime={formatTime} />)}
          {!queueStatus?.running && sortedDone.length === 0 && <div className="empty-state">暂无任务，在下方输入 prompt 开始</div>}

          <div className="section-header" style={{ marginTop: 8 }}>
            排队中 ({pendingTasks.length})<span className="section-header__line"></span>
            <button className="topbar__btn" onClick={handlePauseResume} style={{ fontSize: 11, marginLeft: "auto" }}>{queueStatus?.paused ? "▶ 恢复队列" : "⏸ 暂停队列"}</button>
          </div>
          {pendingTasks.map(t => (
            <TaskCard key={t.id} task={t} isPending draggable formatTime={formatTime}
              onDelete={handleDelete} onDragStart={handleDragStart} onDragEnd={handleDragEnd}
              onDragOver={handleDragOver} onDragLeave={handleDragLeave} onDrop={handleDrop} />
          ))}
          {pendingTasks.length === 0 && <div className="empty-state">队列为空，添加任务开始排队</div>}
          <div style={{ minHeight: 12 }}></div>
        </div>
      </div>

      {/* Bottom Input Area */}
      <div className="bottom-bar" ref={bottomBarRef} onDragOver={handleFileDragOver} onDrop={handleFileDrop} onPaste={handlePaste}>
        <div className="bottom-bar__inner container">
          {/* Uploaded files bar */}
          {(refs.length > 0 || uploading.size > 0) && (
            <div className="uploaded-files">
              {/* Uploading files with progress */}
              {Array.from(uploading.entries()).map(([name, entry]) => (
                <span key={name} className="uploaded-file uploaded-file--uploading" title="上传中，无法预览">
                  <span className="upload-spinner" style={{ '--pct': `${entry.progress}%` } as React.CSSProperties}></span>
                  {name}
                  <span className="uploaded-file__pct">{entry.progress}%</span>
                </span>
              ))}
              {/* Ready files */}
              {refs.map((ref, i) => (
                <span key={i} className="uploaded-file uploaded-file--ready" onClick={() => setPreviewRef(ref)} title="点击预览">
                  {refEmoji(ref.type)} {ref.filename}
                  <span className="uploaded-file__remove" onClick={(e) => { e.stopPropagation(); removeRef(i); }}>×</span>
                </span>
              ))}
            </div>
          )}

          {/* Row 1: full-width textarea */}
          <div className="bottom-bar__prompt-wrapper">
            <textarea ref={textareaRef} className="bottom-bar__prompt"
              placeholder="描述你想生成的视频内容... 输入 @ 引用已上传文件，支持拖拽/粘贴上传"
              rows={1} value={prompt} onChange={handlePromptChange}
              onKeyDown={e => {
                if (e.key === "Enter" && !e.shiftKey && !showMention) { e.preventDefault(); handleSubmit(); }
                if (e.key === "Escape" && showMention) setShowMention(false);
              }} />
            {showMention && (
              <div className="mention-popup">
                {filteredMentions.length === 0 ? (
                  <div className="mention-popup__empty">无匹配文件</div>
                ) : (filteredMentions.map((ref, i) => (
                  <div key={i} className="mention-popup__item" onClick={() => insertMention(ref)}>
                    <span className="mention-popup__icon">{refEmoji(ref.type)}</span>
                    <span className="mention-popup__name">{ref.filename}</span>
                    <span className="mention-popup__type">{ref.type.toUpperCase()}</span>
                  </div>
                )))}
              </div>
            )}
          </div>
          <input ref={fileInputRef} type="file" style={{ display: "none" }} multiple accept="image/*,video/*,audio/*" onChange={handleFileInput} />

          {/* Row 2: all controls */}
          <div className="bottom-bar__controls">
            <div className="bottom-bar__upload" onClick={() => fileInputRef.current?.click()} title="上传参考文件（也支持拖拽/粘贴）">+</div>
            <div className={`mode-hint ${(hasRefs || isUploading) ? "mode-hint--multimodal" : "mode-hint--text2video"}`}>模式: <span>{modeLabel}</span></div>
            <Dropdown label="模型" value={modelVersion} onChange={setModelVersion}
              options={[
                { value: "seedance2.0fast", label: "Seedance 2.0 Fast", icon: "https://p26-dreamina-sign.byteimg.com/tos-cn-i-tb4s082cfz/sd20_avg~tplv-tb4s082cfz-image.image?lk3s=8e790bc3&x-expires=1811042195&x-signature=K%2BiRclxpFfQIvRfh8yJsCHgoP10%3D" },
                { value: "seedance2.0", label: "Seedance 2.0", icon: "https://p26-dreamina-sign.byteimg.com/tos-cn-i-tb4s082cfz/sd20_avg~tplv-tb4s082cfz-image.image?lk3s=8e790bc3&x-expires=1811042195&x-signature=K%2BiRclxpFfQIvRfh8yJsCHgoP10%3D" },
              ]} />
            <Stepper label="时长" value={duration} min={4} max={15} onChange={setDuration} suffix="s"
              icon={<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>} />
            <Dropdown label="比例" value={ratio} onChange={setRatio}
              options={[
                { value: "1:1", label: "1:1", ratioW: 1, ratioH: 1 },
                { value: "3:4", label: "3:4", ratioW: 3, ratioH: 4 },
                { value: "4:3", label: "4:3", ratioW: 4, ratioH: 3 },
                { value: "16:9", label: "16:9", ratioW: 16, ratioH: 9 },
                { value: "9:16", label: "9:16", ratioW: 9, ratioH: 16 },
                { value: "21:9", label: "21:9", ratioW: 21, ratioH: 9 },
              ]} />
            {prompt.length > MAX_PROMPT_LENGTH * 0.8 && (
              <span className={`prompt-counter ${prompt.length > MAX_PROMPT_LENGTH ? "prompt-counter--over" : ""}`}>
                {prompt.length}/{MAX_PROMPT_LENGTH}
              </span>
            )}
            <div className="bottom-bar__right">
              <span className="cost-badge">✦ {estimatedCost}</span>
              <button className="submit-btn" onClick={handleSubmit} disabled={submitting || !prompt.trim() || isUploading || prompt.length > MAX_PROMPT_LENGTH}>
                {isUploading ? `上传中 ${overallProgress}%` : submitting ? "提交中..." : "加入队列"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

// ===== Ratio preview icon =====
function RatioPreview({ w, h }: { w: number; h: number }) {
  const BOX = 18; // outer box size in px
  const PAD = 2;  // padding inside the box
  const maxDim = Math.max(w, h);
  const iw = Math.round((w / maxDim) * (BOX - PAD * 2));
  const ih = Math.round((h / maxDim) * (BOX - PAD * 2));
  return (
    <span className="ratio-preview">
      <span className="ratio-preview__inner" style={{ width: iw, height: ih }} />
    </span>
  );
}

// ===== Stepper with slider popup =====
function Stepper({ label, value, min, max, onChange, suffix, icon }: {
  label: string; value: number; min: number; max: number; onChange: (v: number) => void; suffix: string; icon?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    function clickOut(e: MouseEvent) { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); }
    document.addEventListener("mousedown", clickOut);
    return () => document.removeEventListener("mousedown", clickOut);
  }, []);

  return (
    <div className="control-group stepper-wrap" ref={ref}>
      <span className="control-label">{label}</span>
      <div className="stepper" onClick={() => setOpen(!open)}>
        {icon && <span className="stepper__icon">{icon}</span>}
        <span className="stepper__val">{value}{suffix}</span>
        <span className="dropdown-arrow" style={{ paddingRight: 4 }}>▾</span>
      </div>
      {open && (
        <div className="slider-popup">
          <input type="range" className="slider-input" min={min} max={max} value={value}
            onChange={e => onChange(Number(e.target.value))} />
          <div className="slider-labels">
            <span>{min}s</span>
            <span>{max}s</span>
          </div>
        </div>
      )}
    </div>
  );
}

// ===== Custom Dropdown =====
function Dropdown({ label, value, onChange, options }: {
  label: string; value: string; onChange: (v: string) => void;
  options: { value: string; label: string; ratioW?: number; ratioH?: number; icon?: string }[];
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const selected = options.find(o => o.value === value) || options[0];

  useEffect(() => {
    function clickOut(e: MouseEvent) { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); }
    document.addEventListener("mousedown", clickOut);
    return () => document.removeEventListener("mousedown", clickOut);
  }, []);

  return (
    <div className="control-group dropdown-wrap" ref={ref}>
      <span className="control-label">{label}</span>
      <div className={`dropdown-trigger ${open ? "dropdown-trigger--open" : ""}`} onClick={() => setOpen(!open)}>
        {selected.icon && <img className="dropdown-icon" src={selected.icon} alt="" />}
        {selected.ratioW != null && selected.ratioH != null && (
          <RatioPreview w={selected.ratioW} h={selected.ratioH} />
        )}
        <span>{selected.label}</span>
        <span className="dropdown-arrow">▾</span>
      </div>
      {open && (
        <div className="dropdown-menu">
          {options.map(o => (
            <div key={o.value}
              className={`dropdown-item ${o.value === value ? "dropdown-item--active" : ""}`}
              onClick={() => { onChange(o.value); setOpen(false); }}>
              {o.icon && <img className="dropdown-icon" src={o.icon} alt="" />}
              {o.ratioW != null && o.ratioH != null && (
                <RatioPreview w={o.ratioW} h={o.ratioH} />
              )}
              {o.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ===== XHR upload with progress =====
function xhrUpload(url: string, file: File, onProgress: (pct: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
    });
    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new Error(`HTTP ${xhr.status}`));
    });
    xhr.addEventListener("error", () => reject(new Error("Network error")));
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    xhr.send(file);
  });
}

// ===== File Preview =====
function FilePreview({ refData, onClose }: { refData: Reference; onClose: () => void }) {
  const [src, setSrc] = useState(refData.cos_url);
  const [useFallback, setUseFallback] = useState(false);

  return (
    <div className="preview-overlay" onClick={onClose}>
      <div className="preview-modal" onClick={e => e.stopPropagation()}>
        <div className="preview-modal__header">
          <span className="preview-modal__title">{refData.filename}</span>
          <button className="preview-modal__close" onClick={onClose}>×</button>
        </div>
        <div className="preview-modal__body">
          {refData.type === "video" ? (
            <video key={src} src={src} controls autoPlay className="preview-media"
              onError={() => { if (!useFallback) { setSrc(rawCosUrl(refData.cos_url)); setUseFallback(true); } }} />
          ) : refData.type === "audio" ? (
            <audio key={src} src={src} controls autoPlay className="preview-audio"
              onError={() => { if (!useFallback) { setSrc(rawCosUrl(refData.cos_url)); setUseFallback(true); } }} />
          ) : (
            <img key={src} src={src} alt={refData.filename} className="preview-media"
              onError={() => { if (!useFallback) { setSrc(rawCosUrl(refData.cos_url)); setUseFallback(true); } }} />
          )}
        </div>
      </div>
    </div>
  );
}

// ===== Task Card =====
function TaskCard({
  task, isActive = false, isPending = false, draggable = false,
  formatTime, onDelete, onDragStart, onDragEnd, onDragOver, onDragLeave, onDrop,
}: {
  task: Task; isActive?: boolean; isPending?: boolean; draggable?: boolean;
  formatTime: (ts: string) => string;
  onDelete?: (id: number) => void;
  onDragStart?: (e: React.DragEvent, taskId: number) => void; onDragEnd?: (e: React.DragEvent) => void;
  onDragOver?: (e: React.DragEvent, taskId: number) => void; onDragLeave?: (e: React.DragEvent) => void;
  onDrop?: (e: React.DragEvent, task: Task) => void;
}) {
  const sc = task.status === "running" ? "status-badge--running" : task.status === "done" ? "status-badge--done" : task.status === "failed" ? "status-badge--failed" : "status-badge--pending";
  const sl = task.status === "running" ? "生成中" : task.status === "done" ? "已完成" : task.status === "failed" ? "失败" : `队列 #${task.position + 1}`;
  const tl = task.type === "text2video" ? "文生视频" : "全能参考";

  return (
    <div className={`task-card ${isActive ? "task-card--active" : ""} ${isPending ? "task-card--pending" : ""}`}
      draggable={draggable}
      onDragStart={draggable ? (e) => onDragStart?.(e, task.id) : undefined}
      onDragEnd={draggable ? (e) => onDragEnd?.(e) : undefined}
      onDragOver={draggable ? (e) => onDragOver?.(e, task.id) : undefined}
      onDragLeave={draggable ? (e) => onDragLeave?.(e) : undefined}
      onDrop={draggable ? (e) => onDrop?.(e, task) : undefined}>
      <div className="task-card__header">
        <span className={`status-badge ${sc}`}><span className={`status-dot ${task.status === "running" ? "status-dot--running" : ""}`}></span>{sl}</span>
        {isPending && <span className="task-card__drag-hint" title="长按拖拽排序">⠿</span>}
        <span className="task-card__time">{formatTime(task.created_at)}</span>
        <span className="task-card__cost">{isPending ? "预计 " : ""}-{calcTaskCost(task.params.duration, task.params.model_version, task.references || [])} 积分</span>
        {task.status === "done" && task.result_url && <a className="task-card__link" href={task.result_url} target="_blank" rel="noopener noreferrer">↗ 在即梦查看</a>}
        {isPending && <div className="task-card__actions"><button className="task-card__action-btn task-card__action-btn--danger" onClick={() => onDelete?.(task.id)}>删除</button></div>}
      </div>
      <div className="task-card__prompt">{task.prompt}</div>
      {task.references && task.references.length > 0 && (
        <div className="task-card__refs">
          {task.references.map((ref, i) => (
            <span key={i} className="task-card__ref">{ref.type === "image" ? "🖼" : ref.type === "video" ? "🎬" : "🎵"} {ref.filename}</span>
          ))}
        </div>
      )}
      <div className="task-card__meta">
        <span className="task-card__tag task-card__tag--type">{tl}</span>
        <span className="task-card__tag">{task.params.model_version}</span>
        <span className="task-card__tag">{task.params.ratio}</span>
        <span className="task-card__tag">{task.params.duration}s</span>
        {task.error_message && <span className="task-card__tag" style={{ color: "var(--danger)" }}>{task.error_message}</span>}
      </div>
    </div>
  );
}
