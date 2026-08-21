import { useEffect, useMemo, useRef, useState } from "react";
import { Upload, FileText, Trash2, Download, Loader2, FolderOpen } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { api, ApiError, downloadReport, type MyReport } from "@/lib/api";
import { cn } from "@/lib/utils";

const fmtSize = (b: number) =>
  b < 1024 ? `${b}B` : b < 1048576 ? `${(b / 1024).toFixed(0)}KB` : `${(b / 1048576).toFixed(1)}MB`;
const fmtDate = (ts: number) =>
  new Date(ts).toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });

// 读文件为 dataURL（含 base64）；后端会剥掉 data: 前缀。
const fileToB64 = (file: File): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });

export function MyReports() {
  const [reports, setReports] = useState<MyReport[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    try {
      setReports(await api.myReports());
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "加载研报列表失败");
    }
  };
  useEffect(() => {
    load();
  }, []);

  const upload = async (files: FileList | File[]) => {
    setBusy(true);
    setErr(null);
    try {
      for (const f of Array.from(files)) {
        const b64 = await fileToB64(f);
        await api.uploadReport(f.name, b64);
      }
      await load();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "上传失败");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (r: MyReport) => {
    if (!confirm(`删除「${r.name}」？（同时从本地归档目录移除）`)) return;
    try {
      await api.deleteReport(r.id);
      await load();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "删除失败");
    }
  };

  const download = async (r: MyReport) => {
    try {
      await downloadReport(r.id, r.name);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "下载失败");
    }
  };

  const grouped = useMemo(() => {
    const g: Record<string, MyReport[]> = {};
    for (const r of reports) (g[r.industry] ||= []).push(r);
    // 「未分类」排最后，其余按条数多→少
    return Object.entries(g).sort((a, b) =>
      a[0] === "未分类" ? 1 : b[0] === "未分类" ? -1 : b[1].length - a[1].length,
    );
  }, [reports]);

  return (
    <div>
      <PageHeader
        title="我的研报"
        subtitle="把自己的研报拖进来归档，自动按行业分类。文件只存在本地部署目录、不上传、不进任何仓库。"
      />

      {/* 上传区 */}
      <GlassCard className="mb-4">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            if (e.dataTransfer.files.length) upload(e.dataTransfer.files);
          }}
          onClick={() => inputRef.current?.click()}
          className={cn(
            "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed py-10 text-center transition-colors",
            drag ? "border-primary bg-primary/10" : "border-border hover:border-primary/50 hover:bg-primary/5",
          )}
        >
          {busy ? (
            <Loader2 className="h-7 w-7 animate-spin text-primary" />
          ) : (
            <Upload className="h-7 w-7 text-primary" />
          )}
          <p className="text-sm font-medium">
            {busy ? "上传中…" : "把研报拖到这里，或点击选择文件"}
          </p>
          <p className="text-xs text-muted-foreground/70">
            支持 PDF / Word / txt / md / 表格 / 图片，单个 ≤ 25MB，可一次多选
          </p>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".pdf,.doc,.docx,.txt,.md,.markdown,.csv,.xls,.xlsx,.ppt,.pptx,.png,.jpg,.jpeg,.webp"
            className="hidden"
            onChange={(e) => {
              if (e.target.files?.length) upload(e.target.files);
              e.target.value = "";
            }}
          />
        </div>
      </GlassCard>

      {err && (
        <div className="mb-4 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          {err}
        </div>
      )}

      {/* 列表（按行业分组） */}
      {reports.length === 0 ? (
        <GlassCard>
          <div className="flex flex-col items-center gap-2 py-10 text-center text-sm text-muted-foreground">
            <FolderOpen className="h-8 w-8 text-muted-foreground/40" />
            还没有归档的研报。把你收集的研报拖进上面的框，会自动按行业分好类。
          </div>
        </GlassCard>
      ) : (
        <div className="space-y-4">
          {grouped.map(([industry, items]) => (
            <GlassCard key={industry}>
              <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
                <span className="rounded bg-primary/15 px-2 py-0.5 text-xs text-primary">{industry}</span>
                <span className="text-xs font-normal text-muted-foreground">{items.length} 份</span>
              </h3>
              <div className="divide-y divide-border/30">
                {items.map((r) => (
                  <div key={r.id} className="flex items-center gap-3 py-2.5">
                    <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{r.name}</p>
                      <p className="text-[11px] text-muted-foreground/60">
                        {fmtSize(r.size)} · {fmtDate(r.ts)}
                      </p>
                    </div>
                    <button
                      onClick={() => download(r)}
                      className="shrink-0 text-muted-foreground/60 hover:text-primary"
                      title="下载"
                    >
                      <Download className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => remove(r)}
                      className="shrink-0 text-muted-foreground/50 hover:text-destructive"
                      title="删除"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </GlassCard>
          ))}
        </div>
      )}

      <Disclaimer />
    </div>
  );
}
