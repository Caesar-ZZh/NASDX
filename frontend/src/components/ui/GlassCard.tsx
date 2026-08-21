import { type ReactNode } from "react";
import { cn } from "@/lib/utils";

interface Props {
  children: ReactNode;
  className?: string;
  glow?: boolean;
  onClick?: () => void;
}

// 玻璃卡：半透明填充 + 发丝边框 + 柔投影 + 顶部内高光（科技玻璃暖橙风的基础容器）。
export function GlassCard({ children, className, glow, onClick }: Props) {
  return (
    <div
      onClick={onClick}
      className={cn(
        "glass p-5",
        glow && "glass-glow",
        onClick && "cursor-pointer transition-transform hover:-translate-y-0.5",
        className,
      )}
    >
      {children}
    </div>
  );
}
