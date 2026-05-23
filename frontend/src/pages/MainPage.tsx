import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import {
  fetchTasks,
  fetchQueueStatus,
  fetchCredit,
  deleteTask,
  reorderTask,
  pauseQueue,
  resumeQueue,
  createTask,
  getPresignedUpload,
} from "../api";
import type { Task, QueueStatus, Reference } from "../api";

const REFRESH_INTERVAL = 15; // seconds

const FILE_LIMITS: Record<string, { maxSize: number; label: string }> = {
  image: { maxSize: 10 * 1024 * 1024, label: "10 MB" },
  video: { maxSize: 200 * 1024 * 1024, label: "200 MB" },
  audio: { maxSize: 10 * 1024 * 1024, label: "10 MB" },
};
const MAX_IMAGE_COUNT = 9;
const MAX_VIDEO_COUNT = 3;
const MAX_AUDIO_COUNT = 3;

function detectType(filename: string): Reference["type"] {
  const ext = filename.split(".").pop()?.toLowerCase() || "";
  if (["mp4", "mov", "webm", "avi"].includes(ext)) return "video";
  if (["mp3", "wav", "aac", "m4a", "ogg"].includes(ext)) return "audio";
  return "image";
}

function validateFile(file: File, refs: Reference[]): string | null {
  const type = detectType(file.name);
  const limit = FILE_LIMITS[type];
  if (file.size > limit.maxSize) {
    return `${file.name} 超过 ${limit.label} 限制 (${(file.size / 1024 / 1024).toFixed(1)} MB)`;
  }
  const counts = { image: 0, video: 0, audio: 0 };
  for (const r of refs) counts[r.type]++;
  counts[type]++;
  if (counts.image > MAX_IMAGE_COUNT) return `图片最多 ${MAX_IMAGE_COUNT} 张`;
  if (counts.video > MAX_VIDEO_COUNT) return `视频最多 ${MAX_VIDEO_COUNT} 个`;
  if (counts.audio > MAX_AUDIO_COUNT) return `音频最多 ${MAX_AUDIO_COUNT} 个`;
  if (type === "audio") {
    return `音频需控制在 2-15 秒内，请确认 ${file.name} 符合要求`;
  }
  return null;
}

export default function MainPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [queueStatus, setQueueStatus] = useState<QueueStatus | null>(null);
  const [credit, setCredit] = useState<number | null>(null);
  const [refs, setRefs] = useState<Reference[]>([]);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [dragId, setDragId] = useState<number | null>(null);

  // Form state
  const [prompt, setPrompt] = useState("");
  const [duration, setDuration] = useState(5);
  const [ratio, setRatio] = useState("16:9");
  const [modelVersion, setModelVersion] = useState("seedance2.0fast");
  const [submitting, setSubmitting] = useState(false);

  // @ mention state
  const [showMention, setShowMention] = useState(false);
  const [mentionFilter, setMentionFilter] = useState("");
  // Preview state
  const [previewRef, setPreviewRef] = useState<Reference | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const bottomBarRef = useRef<HTMLDivElement>(null);

  const hasRefs = refs.length > 0;
  const modeLabel = hasRefs ? "全能参考 (multimodal2video)" : "文生视频 (text2video)";

  const refresh = useCallback(async () => {
    try {
      const [taskData, qs, creditData] = await Promise.all([
        fetchTasks(),
        fetchQueueStatus(),
        fetchCredit(),
      ]);
      setTasks(taskData);
      setQueueStatus(qs);
      if (creditData?.total_credit) setCredit(creditData.total_credit);
      setLastRefresh(new Date());
    } catch (err) {
      console.error("Refresh failed", err);
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, REFRESH_INTERVAL * 1000);
    return () => clearInterval(timer);
  }, [refresh]);

  // ===== Upload helpers =====

  async function uploadFiles(files: FileList | File[]) {
    const arr = Array.from(files);
    for (const file of arr) {
      // Validate
      const error = validateFile(file, refs);
      if (error) {
        alert(error);
        continue;
      }
      try {
        const { upload_url, cos_url } = await getPresignedUpload(file.name);
        await fetch(upload_url, {
          method: "PUT",
          body: file,
          headers: { "Content-Type": file.type || "application/octet-stream" },
        });
        const type = detectType(file.name);
        setRefs(prev => [...prev, { type, cos_url, filename: file.name }]);
      } catch (err: any) {
        alert(`${file.name} 上传失败: ${err.message}`);
      }
    }
  }

  async function handleFileInput(e: React.ChangeEvent<HTMLInputElement>) {
    if (e.target.files && e.target.files.length > 0) {
      await uploadFiles(e.target.files);
    }
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  // Drag file upload to bottom bar
  function handleFileDragOver(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = "copy";
  }

  function handleFileDrop(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      uploadFiles(e.dataTransfer.files);
    }
  }

  // Paste upload
  function handlePaste(e: React.ClipboardEvent) {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files: File[] = [];
    for (const item of Array.from(items)) {
      if (item.kind === "file") {
        const file = item.getAsFile();
        if (file) files.push(file);
      }
    }
    if (files.length > 0) {
      e.preventDefault();
      uploadFiles(files);
    }
  }

  // ===== @ mention =====

  function handlePromptChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    const value = e.target.value;
    setPrompt(value);

    // Detect @ trigger
    const cursorPos = e.target.selectionStart || 0;
    const textBefore = value.slice(0, cursorPos);
    const atMatch = textBefore.match(/@([^\s@]*)$/);

    if (atMatch && refs.length > 0) {
      setMentionFilter(atMatch[1].toLowerCase());
      setShowMention(true);
    } else {
      setShowMention(false);
    }
  }

  function insertMention(ref: Reference) {
    const cursorPos = textareaRef.current?.selectionStart || prompt.length;
    const textBefore = prompt.slice(0, cursorPos);
    const textAfter = prompt.slice(cursorPos);
    const atIdx = textBefore.lastIndexOf("@");
    const newPrompt = textBefore.slice(0, atIdx) + ref.filename + " " + textAfter;
    setPrompt(newPrompt);
    setShowMention(false);
    textareaRef.current?.focus();
  }

  const filteredMentions = useMemo(() => {
    if (!mentionFilter) return refs;
    return refs.filter(r => r.filename.toLowerCase().includes(mentionFilter));
  }, [refs, mentionFilter]);

  // ===== Submit =====

  async function handleSubmit() {
    if (!prompt.trim()) return;
    setSubmitting(true);
    try {
      await createTask({
        prompt: prompt.trim(),
        duration,
        ratio,
        model_version: modelVersion,
        references: refs,
      });
      setPrompt("");
      setRefs([]);
      setShowMention(false);
      refresh();
    } catch (err: any) {
      alert(err.message || "提交失败");
    } finally {
      setSubmitting(false);
    }
  }

  function removeRef(index: number) {
    setRefs(prev => prev.filter((_, i) => i !== index));
  }

  async function handleDelete(id: number) {
    try {
      await deleteTask(id);
      refresh();
    } catch (err: any) {
      alert(err.message);
    }
  }

  async function handleReorder(taskId: number, targetPosition: number) {
    try {
      await reorderTask(taskId, targetPosition);
      refresh();
    } catch (err: any) {
      alert(err.message);
    }
  }

  async function handlePauseResume() {
    try {
      if (queueStatus?.paused) {
        await resumeQueue();
      } else {
        await pauseQueue();
      }
      refresh();
    } catch (err: any) {
      alert(err.message);
    }
  }

  // Task drag handlers
  function handleDragStart(e: React.DragEvent, taskId: number) {
    setDragId(taskId);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(taskId));
    (e.currentTarget as HTMLElement).classList.add("task-card--dragging");
  }

  function handleDragEnd(e: React.DragEvent) {
    setDragId(null);
    (e.currentTarget as HTMLElement).classList.remove("task-card--dragging");
  }

  function handleDragOver(e: React.DragEvent, taskId: number) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (dragId !== null && dragId !== taskId) {
      (e.currentTarget as HTMLElement).classList.add("task-card--drag-over");
    }
  }

  function handleDragLeave(e: React.DragEvent) {
    (e.currentTarget as HTMLElement).classList.remove("task-card--drag-over");
  }

  function handleDrop(e: React.DragEvent, targetTask: Task) {
    e.preventDefault();
    (e.currentTarget as HTMLElement).classList.remove("task-card--drag-over");
    const draggedId = Number(e.dataTransfer.getData("text/plain"));
    if (draggedId && draggedId !== targetTask.id) {
      handleReorder(draggedId, targetTask.position);
    }
    setDragId(null);
  }

  const sortedDone = tasks
    .filter(t => t.status === "done" || t.status === "failed")
    .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
  const pendingTasks = tasks
    .filter(t => t.status === "pending")
    .sort((a, b) => a.position - b.position);

  function formatTime(ts: string) {
    const d = new Date(ts + "Z");
    const now = new Date();
    const diff = now.getTime() - d.getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "刚刚";
    if (mins < 60) return `${mins} 分钟前`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours} 小时前`;
    return `${Math.floor(hours / 24)} 天前`;
  }

  function refIconEmoji(type: string) {
    switch (type) {
      case "image": return "🖼";
      case "video": return "🎬";
      case "audio": return "🎵";
      default: return "?";
    }
  }

  return (
    <>
      {previewRef && (
        <FilePreview refData={previewRef} onClose={() => setPreviewRef(null)} />
      )}

      {/* Top Bar */}
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
                更新时间:
                <span className="topbar__refresh-time">
                  {lastRefresh.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                </span>
                <span className="topbar__refresh-tooltip">数据每 {REFRESH_INTERVAL} 秒自动刷新</span>
              </span>
            )}
            {credit !== null && <span className="topbar__credit">✦ {credit.toLocaleString()} 积分</span>}
            <button className="topbar__btn" onClick={() => { localStorage.removeItem("token"); window.location.reload(); }}>退出</button>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="main">
        <div className="main__inner container">

          {/* Running / Done section */}
          <div className="section-header">
            生成中 / 已完成
            <span className="section-header__line"></span>
          </div>

          {queueStatus?.running && (
            <TaskCard task={queueStatus.running} isActive formatTime={formatTime} />
          )}

          {sortedDone.map(task => (
            <TaskCard key={task.id} task={task} formatTime={formatTime} />
          ))}

          {!queueStatus?.running && sortedDone.length === 0 && (
            <div className="empty-state">暂无任务，在下方输入 prompt 开始</div>
          )}

          {/* Pending section */}
          <div className="section-header" style={{ marginTop: 8 }}>
            排队中 ({pendingTasks.length})
            <span className="section-header__line"></span>
            <button className="topbar__btn" onClick={handlePauseResume} style={{ fontSize: 11, marginLeft: "auto" }}>
              {queueStatus?.paused ? "▶ 恢复队列" : "⏸ 暂停队列"}
            </button>
          </div>

          {pendingTasks.map((task) => (
            <TaskCard
              key={task.id}
              task={task}
              isPending
              draggable
              formatTime={formatTime}
             
              onDelete={handleDelete}
              onDragStart={handleDragStart}
              onDragEnd={handleDragEnd}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
            />
          ))}

          {pendingTasks.length === 0 && (
            <div className="empty-state">队列为空，添加任务开始排队</div>
          )}

          <div style={{ minHeight: 12 }}></div>
        </div>
      </div>

      {/* Bottom Input Area */}
      <div
        className="bottom-bar"
        ref={bottomBarRef}
        onDragOver={handleFileDragOver}
        onDrop={handleFileDrop}
        onPaste={handlePaste}
      >
        <div className="bottom-bar__inner container">
          {/* Uploaded files bar */}
          {refs.length > 0 && (
            <div className="uploaded-files">
              {refs.map((ref, i) => (
                <span
                  key={i}
                  className="uploaded-file"
                  onClick={() => setPreviewRef(ref)}
                  title="点击预览"
                >
                  {refIconEmoji(ref.type)} {ref.filename}
                  <span className="uploaded-file__remove" onClick={(e) => { e.stopPropagation(); removeRef(i); }}>×</span>
                </span>
              ))}
            </div>
          )}

          {/* Prompt input row */}
          <div className="bottom-bar__input-row">
            <div className="bottom-bar__prompt-wrapper">
              <textarea
                ref={textareaRef}
                className="bottom-bar__prompt"
                placeholder="描述你想生成的视频内容... 输入 @ 引用已上传文件，支持拖拽/粘贴上传"
                rows={1}
                value={prompt}
                onChange={handlePromptChange}
                onKeyDown={e => {
                  if (e.key === "Enter" && !e.shiftKey && !showMention) {
                    e.preventDefault();
                    handleSubmit();
                  }
                  if (e.key === "Escape" && showMention) {
                    setShowMention(false);
                  }
                }}
              />
              {/* @ mention popup */}
              {showMention && (
                <div className="mention-popup">
                  {filteredMentions.length === 0 ? (
                    <div className="mention-popup__empty">无匹配文件</div>
                  ) : (
                    filteredMentions.map((ref, i) => (
                      <div
                        key={i}
                        className="mention-popup__item"
                        onClick={() => insertMention(ref)}
                      >
                        <span className="mention-popup__icon">{refIconEmoji(ref.type)}</span>
                        <span className="mention-popup__name">{ref.filename}</span>
                        <span className="mention-popup__type">{ref.type.toUpperCase()}</span>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
            <input
              ref={fileInputRef}
              type="file"
              style={{ display: "none" }}
              multiple
              accept="image/*,video/*,audio/*"
              onChange={handleFileInput}
            />
            <div className="bottom-bar__upload" onClick={() => fileInputRef.current?.click()} title="上传参考文件（也支持拖拽/粘贴）">+</div>
            <button className="submit-btn" onClick={handleSubmit} disabled={submitting || !prompt.trim()}>
              {submitting ? "提交中..." : "加入队列"}
            </button>
          </div>

          {/* Controls row */}
          <div className="bottom-bar__controls">
            <div className="control-group">
              <span className="control-label">模型</span>
              <select className="control-select" value={modelVersion} onChange={e => setModelVersion(e.target.value)}>
                <option value="seedance2.0fast">seedance2.0fast</option>
                <option value="seedance2.0">seedance2.0</option>
              </select>
            </div>
            <div className="control-group">
              <span className="control-label">时长</span>
              <select className="control-select" value={duration} onChange={e => setDuration(Number(e.target.value))}>
                <option value={4}>4s</option>
                <option value={5}>5s</option>
                <option value={8}>8s</option>
                <option value={10}>10s</option>
                <option value={15}>15s</option>
              </select>
            </div>
            <div className="control-group">
              <span className="control-label">比例</span>
              <select className="control-select" value={ratio} onChange={e => setRatio(e.target.value)}>
                <option value="1:1">1:1</option>
                <option value="3:4">3:4</option>
                <option value="4:3">4:3</option>
                <option value="16:9">16:9</option>
                <option value="9:16">9:16</option>
                <option value="21:9">21:9</option>
              </select>
            </div>
            <div className={`mode-hint ${hasRefs ? "mode-hint--multimodal" : "mode-hint--text2video"}`}>
              模式: <span>{modeLabel}</span>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

// ===== File Preview Modal =====
function FilePreview({ refData, onClose }: { refData: Reference; onClose: () => void }) {
  const isVideo = refData.type === "video";
  const isAudio = refData.type === "audio";
  const src = refData.cos_url;

  return (
    <div className="preview-overlay" onClick={onClose}>
      <div className="preview-modal" onClick={e => e.stopPropagation()}>
        <div className="preview-modal__header">
          <span className="preview-modal__title">{refData.filename}</span>
          <button className="preview-modal__close" onClick={onClose}>×</button>
        </div>
        <div className="preview-modal__body">
          {isVideo ? (
            <video src={src} controls autoPlay className="preview-media" />
          ) : isAudio ? (
            <audio src={src} controls autoPlay className="preview-audio" />
          ) : (
            <img src={src} alt={refData.filename} className="preview-media" />
          )}
        </div>
      </div>
    </div>
  );
}

// ===== Task Card =====
function TaskCard({
  task, isActive = false, isPending = false, draggable = false,
  formatTime, onDelete,
  onDragStart, onDragEnd, onDragOver, onDragLeave, onDrop,
}: {
  task: Task; isActive?: boolean; isPending?: boolean; draggable?: boolean;
  formatTime: (ts: string) => string;
  onDelete?: (id: number) => void;
  onDragStart?: (e: React.DragEvent, taskId: number) => void;
  onDragEnd?: (e: React.DragEvent) => void;
  onDragOver?: (e: React.DragEvent, taskId: number) => void;
  onDragLeave?: (e: React.DragEvent) => void;
  onDrop?: (e: React.DragEvent, task: Task) => void;
}) {
  const statusClass =
    task.status === "running" ? "status-badge--running"
    : task.status === "done" ? "status-badge--done"
    : task.status === "failed" ? "status-badge--failed"
    : "status-badge--pending";

  const statusLabel =
    task.status === "running" ? "生成中"
    : task.status === "done" ? "已完成"
    : task.status === "failed" ? "失败"
    : `队列 #${task.position + 1}`;

  const typeLabel = task.type === "text2video" ? "文生视频" : "全能参考";

  const refEmoji = (type: string) => type === "image" ? "🖼" : type === "video" ? "🎬" : "🎵";

  return (
    <div
      className={`task-card ${isActive ? "task-card--active" : ""} ${isPending ? "task-card--pending" : ""}`}
      draggable={draggable}
      onDragStart={draggable ? (e) => onDragStart?.(e, task.id) : undefined}
      onDragEnd={draggable ? (e) => onDragEnd?.(e) : undefined}
      onDragOver={draggable ? (e) => onDragOver?.(e, task.id) : undefined}
      onDragLeave={draggable ? (e) => onDragLeave?.(e) : undefined}
      onDrop={draggable ? (e) => onDrop?.(e, task) : undefined}
    >
      <div className="task-card__header">
        <span className={`status-badge ${statusClass}`}>
          <span className={`status-dot ${task.status === "running" ? "status-dot--running" : ""}`}></span>
          {statusLabel}
        </span>
        {isPending && <span className="task-card__drag-hint" title="长按拖拽排序">⠿</span>}
        <span className="task-card__time">{formatTime(task.created_at)}</span>
        <span className="task-card__cost">{isPending ? "预计 " : ""}-15 积分</span>

        {task.status === "done" && task.result_url && (
          <a className="task-card__link" href={task.result_url} target="_blank" rel="noopener noreferrer">
            ↗ 在即梦查看
          </a>
        )}

        {isPending && (
          <div className="task-card__actions">
            <button className="task-card__action-btn task-card__action-btn--danger" onClick={() => onDelete?.(task.id)}>删除</button>
          </div>
        )}
      </div>

      <div className="task-card__prompt">{task.prompt}</div>

      {task.references && task.references.length > 0 && (
        <div className="task-card__refs">
          {task.references.map((ref, i) => (
            <span key={i} className="task-card__ref">
              {refEmoji(ref.type)} {ref.filename}
            </span>
          ))}
        </div>
      )}

      <div className="task-card__meta">
        <span className="task-card__tag task-card__tag--type">{typeLabel}</span>
        <span className="task-card__tag">{task.params.model_version}</span>
        <span className="task-card__tag">{task.params.ratio}</span>
        <span className="task-card__tag">{task.params.duration}s</span>
        {task.error_message && (
          <span className="task-card__tag" style={{ color: "var(--danger)" }}>{task.error_message}</span>
        )}
      </div>
    </div>
  );
}
