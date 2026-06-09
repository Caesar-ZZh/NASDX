"""
NASDX — 扫描 + 同步一体化脚本
每次定时任务触发时：
  1. 运行 ETF50 技术面扫描
  2. 自动同步最新 JSON 数据到 Streamlit Cloud (deploy 分支)

用法：
  python scan_and_sync.py          # 扫描 + 同步
  python scan_and_sync.py --no-sync  # 只扫描，不同步
"""
import sys, os, subprocess, glob
from pathlib import Path
from datetime import datetime

# 代理 patch
import requests as _req
_r = _req.get
def _p(url, **kw):
    if 'eastmoney' in url:
        s = _req.Session(); s.trust_env = True
        return s.get(url, **kw)
    return _r(url, **kw)
_req.get = _p

ROOT = Path(__file__).parent
PYTHON = sys.executable

NO_SYNC = "--no-sync" in sys.argv


def run_etf50_scan():
    """运行 ETF50 技术面扫描"""
    print(f"\n{'='*55}")
    print(f"  ETF50 扫描  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}\n")

    import builtins, io
    orig = builtins.print
    scan_src = (ROOT / "scan_etf50.py").read_text(encoding="utf-8")
    ns = {"__name__": "__scan__", "__file__": str(ROOT / "scan_etf50.py")}
    exec(compile(scan_src, "scan_etf50.py", "exec"), ns)


def sync_to_cloud():
    """把最新报告 JSON 同步到 deploy 分支"""
    print(f"\n{'─'*55}")
    print("  同步数据到 Streamlit Cloud...")

    try:
        # 1. 切换到 deploy 分支
        subprocess.run(["git", "checkout", "deploy"], cwd=ROOT,
                       capture_output=True, check=True)

        # 2. 添加所有 json 报告
        json_files = list(ROOT.glob("reports/*.json"))
        if not json_files:
            print("  无 JSON 数据可同步")
            return

        subprocess.run(["git", "add", "-f"] + [str(f) for f in json_files],
                       cwd=ROOT, capture_output=True)

        # 3. 检查是否有变化
        result = subprocess.run(["git", "diff", "--cached", "--quiet"],
                                 cwd=ROOT, capture_output=True)
        if result.returncode == 0:
            print("  数据无变化，跳过同步")
            return

        # 4. 提交
        msg = f"data: 自动同步扫描数据 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        subprocess.run(["git", "commit", "-m", msg], cwd=ROOT,
                       capture_output=True, check=True)

        # 5. 推送
        result = subprocess.run(["git", "push", "origin", "deploy"],
                                 cwd=ROOT, capture_output=True, text=True)
        if result.returncode == 0:
            print("  ✅ 同步成功！Cloud 将在 1-2 分钟内更新")
        else:
            print(f"  ❌ 推送失败: {result.stderr[:100]}")

    except subprocess.CalledProcessError as e:
        print(f"  ❌ Git 操作失败: {e}")
    finally:
        # 始终切回 master
        subprocess.run(["git", "checkout", "master"], cwd=ROOT,
                       capture_output=True)
        print("  已切回 master 分支")


if __name__ == "__main__":
    run_etf50_scan()

    if not NO_SYNC:
        sync_to_cloud()
    else:
        print("\n[--no-sync] 跳过同步")

    print(f"\n完成  {datetime.now().strftime('%H:%M:%S')}\n")
