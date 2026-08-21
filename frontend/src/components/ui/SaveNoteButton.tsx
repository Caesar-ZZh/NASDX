import { useState } from "react";
import { Check, BookmarkPlus } from "lucide-react";
import { addNote } from "@/lib/notes";

// 把一段 AI 结果存入「研究记录」（沉淀）。存本地、不上传。
export function SaveNoteButton({ kind, title, content }: { kind: string; title: string; content: string }) {
  const [saved, setSaved] = useState(false);
  if (!content.trim()) return null;
  return (
    <button
      onClick={() => { addNote(kind, title, content); setSaved(true); }}
      disabled={saved}
      className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-primary disabled:opacity-60"
    >
      {saved ? (<><Check className="h-3.5 w-3.5" /> 已存入沉淀</>) : (<><BookmarkPlus className="h-3.5 w-3.5" /> 存入沉淀</>)}
    </button>
  );
}
