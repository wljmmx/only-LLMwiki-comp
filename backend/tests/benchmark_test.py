#!/usr/bin/env python3
"""
MarkItDown vs MinerU 真实对比测试脚本
测试 4 个混乱格式文件：DOCX, XLSX, HTML, Markdown
"""
import time
import json
import os
import sys
import subprocess
from pathlib import Path

BENCHMARK_DIR = Path(__file__).parent / "fixtures" / "benchmark"
OUTPUT_DIR = Path(__file__).parent / "benchmark_results"
OUTPUT_DIR.mkdir(exist_ok=True)

TEST_FILES = {
    "chaos_incident_report.docx": "Word 文档（混乱格式：多字体/颜色/嵌套列表/表格/代码块）",
    "chaos_server_inventory.xlsx": "Excel 表格（合并单元格/多Sheet/隐藏行/颜色标记）",
    "chaos_nginx_guide.html": "HTML 页面（内联CSS/DIV布局/代码块/表格/警告框）",
    "chaos_ops_scripts.md": "Markdown 文档（bash/sql代码块/表格/嵌套列表/引用/告警规则）",
}

results = []


def test_markitdown():
    """测试 MarkItDown 转换"""
    print("=" * 80)
    print("MarkItDown 测试")
    print("=" * 80)
    
    from markitdown import MarkItDown
    md = MarkItDown()
    
    for filename, description in TEST_FILES.items():
        filepath = BENCHMARK_DIR / filename
        if not filepath.exists():
            print(f"  SKIP {filename}: file not found")
            continue
        
        print(f"\n  [{filename}] {description}")
        t0 = time.time()
        try:
            result = md.convert(str(filepath))
            elapsed = time.time() - t0
            text = result.text_content
            lines = text.count("\n") + 1
            chars = len(text)
            
            # 保存输出
            out_path = OUTPUT_DIR / f"markitdown_{filename}.md"
            out_path.write_text(text, encoding="utf-8")
            
            print(f"    耗时: {elapsed*1000:.0f}ms | {lines}行 | {chars}字符")
            print(f"    输出: {out_path}")
            
            results.append({
                "tool": "MarkItDown",
                "file": filename,
                "description": description,
                "time_ms": round(elapsed * 1000, 1),
                "lines": lines,
                "chars": chars,
                "status": "OK",
                "output_path": str(out_path),
            })
        except Exception as e:
            elapsed = time.time() - t0
            print(f"    失败 ({elapsed*1000:.0f}ms): {e}")
            results.append({
                "tool": "MarkItDown",
                "file": filename,
                "description": description,
                "time_ms": round(elapsed * 1000, 1),
                "status": f"FAILED: {e}",
            })


def test_mineru():
    """测试 MinerU 转换（使用 CLI 模式）"""
    print("\n" + "=" * 80)
    print("MinerU 测试")
    print("=" * 80)
    
    for filename, description in TEST_FILES.items():
        filepath = BENCHMARK_DIR / filename
        if not filepath.exists():
            print(f"  SKIP {filename}: file not found")
            continue
        
        out_dir = OUTPUT_DIR / f"mineru_{Path(filename).stem}"
        out_dir.mkdir(exist_ok=True)
        
        print(f"\n  [{filename}] {description}")
        t0 = time.time()
        try:
            # MinerU CLI: mineru -p <input> -o <output_dir> -b pipeline
            cmd = ["/root/.pyenv/versions/3.12.13/bin/mineru", "-p", str(filepath), "-o", str(out_dir), "-b", "pipeline"]
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )
            elapsed = time.time() - t0
            
            if proc.returncode != 0:
                stderr = proc.stderr.strip()
                print(f"    失败 ({elapsed:.1f}s): {stderr[:200]}")
                results.append({
                    "tool": "MinerU",
                    "file": filename,
                    "description": description,
                    "time_ms": round(elapsed * 1000, 1),
                    "status": f"FAILED: {stderr[:200]}",
                })
                continue
            
            # 查找输出 Markdown 文件
            md_files = list(out_dir.rglob("*.md"))
            if md_files:
                md_file = md_files[0]
                text = md_file.read_text(encoding="utf-8")
                lines = text.count("\n") + 1
                chars = len(text)
                
                # 复制到统一命名
                dest = OUTPUT_DIR / f"mineru_{filename}.md"
                dest.write_text(text, encoding="utf-8")
                
                print(f"    耗时: {elapsed:.1f}s | {lines}行 | {chars}字符")
                print(f"    输出: {dest}")
                
                results.append({
                    "tool": "MinerU",
                    "file": filename,
                    "description": description,
                    "time_ms": round(elapsed * 1000, 1),
                    "lines": lines,
                    "chars": chars,
                    "status": "OK",
                    "output_path": str(dest),
                })
            else:
                # 检查是否有其他输出
                all_files = list(out_dir.rglob("*"))
                print(f"    产出文件: {[f.name for f in all_files if f.is_file()]}")
                results.append({
                    "tool": "MinerU",
                    "file": filename,
                    "description": description,
                    "time_ms": round(elapsed * 1000, 1),
                    "status": "NO_MD_OUTPUT",
                    "output_files": [f.name for f in all_files if f.is_file()],
                })
                
        except subprocess.TimeoutExpired:
            elapsed = time.time() - t0
            print(f"    超时 (120s)")
            results.append({
                "tool": "MinerU",
                "file": filename,
                "description": description,
                "time_ms": round(elapsed * 1000, 1),
                "status": "TIMEOUT",
            })
        except Exception as e:
            elapsed = time.time() - t0
            print(f"    异常 ({elapsed:.1f}s): {e}")
            results.append({
                "tool": "MinerU",
                "file": filename,
                "description": description,
                "time_ms": round(elapsed * 1000, 1),
                "status": f"ERROR: {e}",
            })


def output_summary():
    """输出对比摘要"""
    print("\n" + "=" * 80)
    print("对比摘要")
    print("=" * 80)
    
    # 按工具分组
    md_results = [r for r in results if r["tool"] == "MarkItDown"]
    mu_results = [r for r in results if r["tool"] == "MinerU"]
    
    print(f"\n{'工具':<12} {'文件':<35} {'状态':<10} {'耗时':>10} {'行数':>8} {'字符':>8}")
    print("-" * 110)
    for r in results:
        status = r["status"]
        time_str = f"{r['time_ms']:.0f}ms" if r["time_ms"] < 1000 else f"{r['time_ms']/1000:.1f}s"
        lines = r.get("lines", "-")
        chars = r.get("chars", "-")
        print(f"{r['tool']:<12} {r['file']:<35} {status:<10} {time_str:>10} {str(lines):>8} {str(chars):>8}")
    
    # 保存 JSON
    summary_path = OUTPUT_DIR / "benchmark_results.json"
    summary_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已保存: {summary_path}")


if __name__ == "__main__":
    test_markitdown()
    test_mineru()
    output_summary()